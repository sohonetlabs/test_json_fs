import argparse
import hashlib
import json
import logging
import os
import platform
import sys
import threading
import time
from errno import ENOENT, EROFS
from functools import lru_cache, wraps
from stat import S_IFDIR, S_IFREG

from cachetools import LRUCache
from fuse import FUSE, FuseOSError, Operations

__version__ = "1.1.0"

FILL_CHAR_MODE = "fill_char"
SEMI_RANDOM_MODE = "semi_random"

# the presence of these files in the root directory of the filesystem, with 0 size will stop
# the spotlight indexing on macOS.
macos_root_empty_files_to_control_caching = [
    ".metadata_never_index",
    ".metadata_never_index_unless_rootfs",
    ".metadata_direct_scope_only",
]


def setup_logging(log_level, log_to_stdout=False):
    log_format = "%(asctime)s - %(levelname)s - %(message)s"

    if log_to_stdout:
        logging.basicConfig(level=log_level, format=log_format)
    else:
        logging.basicConfig(filename="jsonfs.log", level=log_level, format=log_format)

    return logging.getLogger(__name__)


def humanize_bytes(bytes, precision=2):
    abbrevs = (
        (1 << 50, "PB"),
        (1 << 40, "TB"),
        (1 << 30, "GB"),
        (1 << 20, "MB"),
        (1 << 10, "KB"),
        (1, "Bytes"),
    )
    if bytes == 1:
        return "1 byte"
    for factor, suffix in abbrevs:
        if bytes >= factor:
            break
    return f"{bytes / factor:.{precision}f} {suffix}"


def parse_size(size):
    units = {
        "B": 1,
        "k": 1024, "K": 1024,
        "M": 1024 * 1024, "m": 1024 * 1024,
        "G": 1024 * 1024 * 1024, "g": 1024 * 1024 * 1024,
        "T": 1024 ** 4, "t": 1024 ** 4,
        "P": 1024 ** 5, "p": 1024 ** 5,
        "E": 1024 ** 6, "e": 1024 ** 6
    }
    
    if isinstance(size, int):
        return size
    
    if size[-1] in units:
        return int(size[:-1]) * units[size[-1]]
    
    return int(size)

class TokenBucket:
    def __init__(self, tokens, time_unit=1.0, fill_rate=None):
        self.tokens = tokens
        self.time_unit = time_unit
        self.fill_rate = tokens if fill_rate is None else fill_rate
        self.timestamp = time.time()
        self.lock = threading.Lock()

    def consume(self, tokens=1):
        with self.lock:
            now = time.time()
            time_passed = now - self.timestamp
            self.tokens += time_passed * (self.fill_rate / self.time_unit)
            if self.tokens > self.fill_rate:
                self.tokens = self.fill_rate
            self.timestamp = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0
            else:
                sleep_time = (tokens - self.tokens) / (self.fill_rate / self.time_unit)
                self.tokens = 0
                return sleep_time


def rate_limited(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.iop_limit > 0:
            sleep_time = self.token_bucket.consume()
            time.sleep(sleep_time)
        if self.rate_limit > 0:
            time.sleep(self.rate_limit)
        return func(self, *args, **kwargs)

    return wrapper


class JSONFileSystem(Operations):
    def __init__(
        self,
        json_data,
        fill_char="\0",
        fill_mode=FILL_CHAR_MODE,
        rate_limit=0,
        iop_limit=0,
        report=True,
        logger=None,
        block_size=1 * 1024 * 1024,
        block_cache_size=1000,
    ):
        self.json_data = json_data
        self.root = json_data[0]  # The first item should be the root directory
        self.now = time.time()
        self.fill_mode = fill_mode
        self.fill_char = fill_char
        self.rate_limit = rate_limit
        self.iop_limit = iop_limit
        self.report = report
        self.token_bucket = TokenBucket(iop_limit) if iop_limit > 0 else None
        self.logger = logger or logging.getLogger(__name__)
        self.block_size = block_size

        # IOPS and data transfer counters
        self.iops_count = 0
        self.bytes_read = 0
        self.stats_lock = threading.Lock()

        # Block cache setup
        self.block_cache = LRUCache(maxsize=block_cache_size)
        self.block_cache_hits = 0
        self.block_cache_misses = 0

        # Pre-generate read buffers for fill_char mode
        if self.fill_mode == FILL_CHAR_MODE:
            self.read_buffers = {
                4096: fill_char.encode() * 4096,
                8192: fill_char.encode() * 8192,
                16384: fill_char.encode() * 16384,
            }

        self.logger.info("Initializing JSONFileSystem")
        self.logger.info(f"Fill mode: {self.fill_mode}")
        self.logger.info(f"Block size: {humanize_bytes(self.block_size)}")
        self.logger.info(f"Block cache size: {self.block_cache.maxsize} blocks")
        self.logger.info(f"Rate limit: {self.rate_limit} seconds")
        self.logger.info(f"IOP limit: {self.iop_limit} IOPS")
        self.logger.debug("Root structure:")
        self._print_structure(self.root, max_depth=2)

        self.total_size = self._calculate_total_size(self.root)
        self.total_files = self._count_files(self.root)
        self.logger.info(f"Total size: {humanize_bytes(self.total_size)} ({self.total_size} bytes)")
        self.logger.info(f"Total files: {self.total_files}")

        # add in files to control caching on macOS
        if platform.system() == "Darwin":
            self._add_macos_control_files()

        # Build flat dictionary for faster lookups
        self.path_map = self._build_path_map(self.root)

        # Start stats reporting thread
        if self.report:
            self.stats_thread = threading.Thread(target=self._report_stats, daemon=True)
            self.stats_thread.start()

    def _add_macos_control_files(self):
        for filename in macos_root_empty_files_to_control_caching:
            self.root["contents"].append(
                {
                    "type": "file",
                    "name": filename,
                    "size": 0,
                }
            )
        self.logger.info("Added macOS control files to root directory")

    def _increment_stats(self, bytes_read=0):
        with self.stats_lock:
            self.iops_count += 1
            self.bytes_read += bytes_read

    def _report_stats(self):
        while True:
            time.sleep(1)  # Report every second
            with self.stats_lock:
                print(
                    f"IOPS: {self.iops_count}, Data transferred: {humanize_bytes(self.bytes_read)}/s ({self.bytes_read} B/s)"
                )
                if self.fill_mode == SEMI_RANDOM_MODE:
                    print(f"{self._cache_info()}")
                self.iops_count = 0
                self.bytes_read = 0

    def _print_structure(self, item, depth=0, max_depth=2):
        if depth > max_depth:
            return
        indent = "  " * depth
        item_type = item.get("type", "unknown")
        item_name = item.get("name", "unnamed")
        item_size = item.get("size", "N/A")
        if isinstance(item_size, int):
            size_str = f"{humanize_bytes(item_size)} ({item_size} bytes)"
        else:
            size_str = str(item_size)
        self.logger.debug(f"{indent}{item_name} ({item_type}, size: {size_str})")
        if item_type == "directory" and "contents" in item:
            for child in item["contents"][:5]:  # Print only first 5 children
                self._print_structure(child, depth + 1, max_depth)
            if len(item["contents"]) > 5:
                self.logger.debug(f"{indent}  ... ({len(item['contents']) - 5} more items)")

    def _calculate_total_size(self, item):
        item_type = item.get("type")
        item_name = item.get("name", "unnamed")
        if item_type == "file":
            size = item.get("size", 0)
            self.logger.debug(f"File: {item_name}, Size: {humanize_bytes(size)} ({size} bytes)")
            return size
        elif item_type == "directory":
            dir_size = sum(self._calculate_total_size(child) for child in item.get("contents", []))
            self.logger.debug(f"Directory: {item_name}, Size: {humanize_bytes(dir_size)} ({dir_size} bytes)")
            return dir_size
        else:
            self.logger.warning(f"Unknown item type: {item_type} for {item_name}")
            return 0

    def _count_files(self, item):
        if "type" not in item:
            return 0
        if item["type"] == "file":
            return 1
        elif item["type"] == "directory":
            return sum(self._count_files(child) for child in item.get("contents", []))
        return 0

    def _build_path_map(self, item, current_path="/"):
        path_map = {current_path: item}
        if item["type"] == "directory":
            for child in item.get("contents", []):
                child_path = os.path.join(current_path, child["name"])
                path_map.update(self._build_path_map(child, child_path))
        return path_map

    @lru_cache(maxsize=1000)
    def _get_item(self, path):
        return self.path_map.get(path)

    def _cache_info(self):
        total = self.block_cache_hits + self.block_cache_misses
        hit_rate = (self.block_cache_hits / total) * 100 if total > 0 else 0
        return (
            f"Block cache: hits: {self.block_cache_hits}, misses: {self.block_cache_misses}, hit rate: {hit_rate:.2f}%"
        )

    @lru_cache(maxsize=1000)
    def _get_block_seed(self, path, block):
        """
        Generate a unique seed for a given file path and block number.
        This method is cached to avoid recalculating seeds for frequently accessed blocks.

        Strategy:
        1. Create a unique seed by combining the file path and block number.
        2. Use MD5 hashing to generate a consistent and well-distributed seed.
        3. Convert the MD5 hash to an integer for use as a random seed.
        """
        base_seed = hashlib.md5(path.encode()).digest()
        block_seed = hashlib.md5(base_seed + block.to_bytes(8, byteorder="big")).digest()
        return int.from_bytes(block_seed, byteorder="big")

    def _generate_block_data(self, path, block):
        """
        Generate or retrieve cached data for a specific block of a file.

        Caching strategy:
        1. Check if the block is in the cache. If so, return it (cache hit).
        2. If not in cache, generate the block data (cache miss).
        3. Store the generated data in the cache for future use.
        4. Return the generated data.

        This strategy ensures that frequently accessed blocks are quickly retrieved from cache,
        while still allowing for deterministic generation of any block when needed.
        """
        cache_key = (path, block)
        if cache_key in self.block_cache:
            self.block_cache_hits += 1
            return self.block_cache[cache_key]

        self.block_cache_misses += 1
        random_seed = self._get_block_seed(path, block)

        block_data = bytearray(self.block_size)
        for i in range(self.block_size):
            random_seed = (random_seed * 1103515245 + 12345) & 0x7FFFFFFF
            block_data[i] = random_seed % 256

        block_data = bytes(block_data)
        self.block_cache[cache_key] = block_data
        return block_data

    @rate_limited
    def read(self, path, size, offset, fh):
        """
        Read data from a file in the virtual filesystem.

        Strategy:
        1. Validate the file path and calculate the actual read size.
        2. For FILL_CHAR_MODE, return a buffer filled with the specified character.
        3. For SEMI_RANDOM_MODE:
           a. Determine which blocks are needed based on the offset and size.
           b. For each required block:
              - Generate or retrieve the block data from cache.
              - Extract the needed portion of the block.
              - Append the extracted data to the result.
        4. Return the assembled data.

        This approach allows for efficient reading of file data, leveraging block caching
        to improve performance for repeated reads of the same file regions.
        """
        self.logger.debug(f"read called for path: {path}, size: {size}, offset: {offset}")

        item = self._get_item(path)
        if item is None or item["type"] != "file":
            self.logger.warning(f"Invalid file path: {path}")
            raise FuseOSError(ENOENT)

        read_size = min(size, item.get("size", 0) - offset)
        self._increment_stats(read_size)
        self.logger.debug(f"Returning {read_size} bytes of data")

        if self.fill_mode == FILL_CHAR_MODE:
            if read_size in self.read_buffers:
                return self.read_buffers[read_size]
            return self.fill_char.encode() * read_size
        elif self.fill_mode == SEMI_RANDOM_MODE:
            start_block = offset // self.block_size
            end_block = (offset + read_size - 1) // self.block_size

            data = bytearray(read_size)
            data_offset = 0

            for block in range(start_block, end_block + 1):
                block_data = self._generate_block_data(path, block)

                # Calculate start and end positions within this block
                block_start = max(0, offset - block * self.block_size)
                block_end = min(self.block_size, offset + read_size - block * self.block_size)

                # Copy required portion of block data
                chunk = block_data[block_start:block_end]
                data[data_offset : data_offset + len(chunk)] = chunk
                data_offset += len(chunk)

            assert len(data) == read_size, f"Data size mismatch: expected {read_size}, got {len(data)}"
            self.logger.debug(self._cache_info())
            
            return bytes(data)

    @rate_limited
    def getattr(self, path, fh=None):
        self._increment_stats()
        self.logger.debug(f"getattr called for path: {path}")
        item = self._get_item(path)
        if item is None:
            self.logger.warning(f"Path not found: {path}")
            raise FuseOSError(ENOENT)

        st = {
            "st_atime": self.now,
            "st_ctime": self.now,
            "st_mtime": self.now,
            "st_nlink": 2,
        }

        if item["type"] == "directory":
            st["st_mode"] = S_IFDIR | 0o555
            st["st_size"] = 4096  # Standard size for directories
        else:
            st["st_mode"] = S_IFREG | 0o444
            st["st_size"] = item.get("size", 0)

        self.logger.debug(f"getattr returned: {st}")
        return st

    @rate_limited
    def readdir(self, path, fh):
        self._increment_stats()
        self.logger.debug(f"readdir called for path: {path}")
        item = self._get_item(path)
        if item is None or item["type"] != "directory":
            self.logger.warning(f"Invalid directory path: {path}")
            raise FuseOSError(ENOENT)

        yield "."
        yield ".."
        for child in item.get("contents", []):
            self.logger.debug(f"Yielding child: {child['name']}")
            yield child["name"]

    def statfs(self, path):
        block_size = 4096
        total_blocks = (self.total_size + block_size - 1) // block_size

        return {
            "f_bsize": block_size,
            "f_frsize": block_size,
            "f_blocks": total_blocks,
            "f_bfree": 0,
            "f_bavail": 0,
            "f_files": max(1, self.total_files),
            "f_ffree": 0,
            "f_favail": 0,
            "f_flag": 0,
            "f_namemax": 255,
        }

    def access(self, path, mode):
        if not self._get_item(path):
            raise FuseOSError(ENOENT)
        return 0

    def opendir(self, path):
        if not self._get_item(path):
            raise FuseOSError(ENOENT)
        return 0

    def releasedir(self, path, fh):
        return 0

    def open(self, path, flags):
        if not self._get_item(path):
            raise FuseOSError(ENOENT)
        return 0

    def release(self, path, fh):
        return 0

    def readlink(self, path):
        raise FuseOSError(ENOENT)  # Assuming no symlinks in the JSON structure

    def utimens(self, path, times=None):
        return 0  # No-op for read-only filesystem

    def chmod(self, path, mode):
        raise FuseOSError(EROFS)

    def chown(self, path, uid, gid):
        raise FuseOSError(EROFS)

    def mknod(self, path, mode, dev):
        raise FuseOSError(EROFS)

    def mkdir(self, path, mode):
        raise FuseOSError(EROFS)

    def unlink(self, path):
        raise FuseOSError(EROFS)

    def rmdir(self, path):
        raise FuseOSError(EROFS)

    def symlink(self, name, target):
        raise FuseOSError(EROFS)

    def rename(self, old, new):
        raise FuseOSError(EROFS)

    def link(self, target, name):
        raise FuseOSError(EROFS)

    def truncate(self, path, length):
        raise FuseOSError(EROFS)


def main():
    parser = argparse.ArgumentParser(description="Mount a JSON file as a read-only filesystem")
    parser.add_argument("json_file", help="Path to the JSON file describing the filesystem")
    parser.add_argument("mount_point", help="Mount point for the filesystem")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Set the logging level (default: INFO)",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=0,
        help="Rate limit in seconds (e.g., 0.1 for 100ms delay)",
    )
    parser.add_argument(
        "--iop-limit",
        type=int,
        default=0,
        help="IOP limit per second (e.g., 100 for 100 IOPS)",
    )
    parser.add_argument(
        "--report-stats",
        action="store_false",
        help="Enable IOPS and data transfer reporting",
    )
    parser.add_argument(
        "--log-to-syslog",
        action="store_true",
        help="Log to syslog instead of stdout",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show the version number and exit",
    )
    parser.add_argument(
        "--block-size",
        type=str,
        default="1M",
        help="Size of blocks for semi-random data generation (e.g., 1M, 2G, 512K). Default: 1M",
    )
    parser.add_argument(
        "--block-cache-size",
        type=int,
        default=1000,
        help="Number of blocks to cache for semi-random data generation. Default: 1000",
    )

    # Add new mutually exclusive group for fill modes
    fill_mode_group = parser.add_mutually_exclusive_group()
    fill_mode_group.add_argument(
        "--fill-char",
        help="Character to fill read data with (default: null byte)",
    )
    fill_mode_group.add_argument(
        "--semi-random",
        action="store_true",
        help="Use semi-random data for file contents",
    )

    args = parser.parse_args()

    log_level = getattr(logging, args.log_level)
    logger = setup_logging(log_level=log_level, log_to_stdout=not args.log_to_syslog)

    logger.info(f"Starting JSONFileSystem version {__version__} with log level: {args.log_level}")

    if args.fill_char and args.semi_random:
        logger.error("Error: Cannot use both --fill-char and --semi-random options.")
        sys.exit(1)

    fill_mode = SEMI_RANDOM_MODE if args.semi_random else FILL_CHAR_MODE
    fill_char = args.fill_char if args.fill_char else "\0"
    block_size = parse_size(args.block_size)

    with open(args.json_file, "r") as f:
        json_data = json.load(f)

    FUSE(
        JSONFileSystem(
            json_data,
            fill_char=fill_char,
            fill_mode=fill_mode,
            rate_limit=args.rate_limit,
            iop_limit=args.iop_limit,
            report=not args.report_stats,
            logger=logger,
            block_size=block_size,
            block_cache_size=args.block_cache_size,
        ),
        args.mount_point,
        nothreads=True,
        foreground=True,
    )


if __name__ == "__main__":
    main()
