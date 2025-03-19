import argparse
import hashlib
import json
import logging
import platform
import random
import sys
import threading
import time
import datetime
from errno import ENOENT, EROFS
from functools import lru_cache
from stat import S_IFDIR, S_IFREG
from pathlib import Path
import os
import unicodedata

from fuse import FUSE, FuseOSError, Operations

__version__ = "1.6.1"

# Constants for fill modes
FILL_CHAR_MODE = "fill_char"
SEMI_RANDOM_MODE = "semi_random"

# Files to control macOS Spotlight indexing
macos_root_empty_files_to_control_caching = [
    ".metadata_never_index",  # Prevents Spotlight from indexing the volume
    ".metadata_never_index_unless_rootfs",  # Prevents indexing unless it's the root filesystem
    ".metadata_direct_scope_only",  # Limits Spotlight to direct scoping only
]


def setup_logging(log_level, log_to_stdout=False):
    """Set up logging configuration."""
    log_format = "%(asctime)s - %(levelname)s - %(message)s"

    if log_to_stdout:
        logging.basicConfig(level=log_level, format=log_format)
    else:
        logging.basicConfig(filename="jsonfs.log", level=log_level, format=log_format)

    return logging.getLogger(__name__)


def humanize_bytes(bytes, precision=2):
    """Convert bytes to a human-readable format."""
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
    """Parse a size string (e.g., '1M', '2G') into bytes."""
    units = {
        "B": 1,
        "k": 1024,
        "K": 1024,
        "M": 1024 * 1024,
        "m": 1024 * 1024,
        "G": 1024 * 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "T": 1024**4,
        "t": 1024**4,
        "P": 1024**5,
        "p": 1024**5,
        "E": 1024**6,
        "e": 1024**6,
    }

    if isinstance(size, int):
        return size

    if size[-1] in units:
        return int(size[:-1]) * units[size[-1]]

    return int(size)


def _unicode_to_named_entities(s):
    # returns the unicode in the form
    # \N { LATIN SMALL LETTER E WITH ACUTE }
    # original: caf\N{LATIN SMALL LETTER E WITH ACUTE}
    return "".join(
        (f"\\N{{{unicodedata.name(char, f'#{ord(char)}')}}}" if not char.isprintable() or ord(char) > 127 else char)
        for char in s
    )


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
        pre_generated_blocks=1000,
        seed=None,
        add_macos_cache_files=True,
        uid=None,
        gid=None,
        mtime=None,
        unicode_normalization="NFD",
    ):
        self.json_data = json_data
        self.root = json_data[0]  # The first item should be the root directory
        self.now = time.time()
        self.fill_mode = fill_mode
        self.fill_char = fill_char
        self.rate_limit = rate_limit
        self.iop_limit = iop_limit
        self.report = report
        self.logger = logger or logging.getLogger(__name__)
        self.block_size = block_size
        self.pre_generated_blocks = pre_generated_blocks
        self.uid = uid
        self.gid = gid
        self.mtime = mtime
        self.unicode_normalization = unicode_normalization

        # Set up consistent random seed
        self.seed = seed if seed is not None else int(4)
        self.random = random.Random(self.seed)
        self.logger.info(f"Using seed: {self.seed}")

        # IOPS and data transfer counters
        self.iops_count = 0
        self.bytes_read = 0
        self.stats_lock = threading.Lock()

        # Generate block cache
        self.block_cache = self._generate_block_cache()

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
        self.logger.info(f"Pre-generated blocks: {self.pre_generated_blocks}")
        self.logger.info(f"Rate limit: {self.rate_limit} seconds")
        self.logger.info(f"IOP limit: {self.iop_limit} IOPS")
        self.logger.debug("Root structure:")
        self._print_structure(self.root, max_depth=2)

        self.total_size = self._calculate_total_size(self.root)
        self.total_files = self._count_files(self.root)
        self.logger.info(f"Total size: {humanize_bytes(self.total_size)} ({self.total_size} bytes)")
        self.logger.info(f"Total files: {self.total_files}")

        # Add macOS control files to prevent caching, do not use plaform as we could be sharing the filesystem
        if add_macos_cache_files:
            self._add_macos_control_files()

        # Build flat dictionary for faster lookups
        self.path_map = self._build_path_map(self.root)

        # Start stats reporting thread
        if self.report:
            self.stats_thread = threading.Thread(target=self._report_stats, daemon=True)
            self.stats_thread.start()

    def _generate_block_cache(self):
        """Generate a cache of pre-generated blocks."""
        self.logger.info(f"Generating {self.pre_generated_blocks} blocks of size {humanize_bytes(self.block_size)}")
        start_generation = time.time()
        cache = []
        for i in range(self.pre_generated_blocks):
            block_data = bytearray(self.block_size)
            block_seed = self.random.randint(0, 2**32 - 1)
            for j in range(self.block_size):
                block_seed = (block_seed * 1103515245 + 12345) & 0x7FFFFFFF
                block_data[j] = block_seed % 256
            cache.append(bytes(block_data))
        end_generation = time.time()
        self.logger.info(f"Block cache generation took {end_generation - start_generation:.2f} seconds")
        return cache

    def _add_macos_control_files(self):
        """Add control files to prevent Spotlight indexing on macOS."""
        for filename in macos_root_empty_files_to_control_caching:
            self.root["contents"].append(
                {
                    "type": "file",
                    "name": filename,
                    "size": 0,
                }
            )
        self.logger.info("Added macOS control files to root directory")
        self.logger.debug("macOS control files added: " + ", ".join(macos_root_empty_files_to_control_caching))

    def _increment_stats(self, bytes_read=0):
        """Increment IOPS and bytes read counters."""
        with self.stats_lock:
            self.iops_count += 1
            self.bytes_read += bytes_read

    def _report_stats(self):
        """Report IOPS and data transfer statistics periodically."""
        while True:
            time.sleep(1)  # Report every second
            with self.stats_lock:
                print(
                    f"IOPS: {self.iops_count}, Data transferred: {humanize_bytes(self.bytes_read)}/s ({self.bytes_read} B/s)"
                )
                self.iops_count = 0
                self.bytes_read = 0

    def _print_structure(self, item, depth=0, max_depth=2):
        """Print the structure of the filesystem (for debugging)."""
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
        self.logger.debug(
            f"{indent}{item_name} ({item_type}, size: {size_str} {_unicode_to_named_entities(item_name)})"
        )
        if item_type == "directory" and "contents" in item:
            for child in item["contents"][:5]:  # Print only first 5 children
                self._print_structure(child, depth + 1, max_depth)
            if len(item["contents"]) > 5:
                self.logger.debug(f"{indent}  ... ({len(item['contents']) - 5} more items)")

    def _calculate_total_size(self, item):
        """Calculate the total size of the filesystem."""
        item_type = item.get("type")
        item_name = item.get("name", "unnamed")
        if item_type == "file":
            size = item.get("size", 0)
            self.logger.debug(
                f"File: {item_name}, Size: {humanize_bytes(size)} ({size} bytes) {_unicode_to_named_entities(item_name)}"
            )
            return size
        elif item_type == "directory":
            dir_size = sum(self._calculate_total_size(child) for child in item.get("contents", []))
            self.logger.debug(
                f"Directory: {item_name}, Size: {humanize_bytes(dir_size)} ({dir_size} bytes) {_unicode_to_named_entities(item_name)}"
            )
            return dir_size
        else:
            self.logger.warning(f"Unknown item type: {item_type} for {item_name}")
            return 0

    def _count_files(self, item):
        """Count the total number of files in the filesystem."""
        if "type" not in item:
            return 0
        if item["type"] == "file":
            return 1
        elif item["type"] == "directory":
            return sum(self._count_files(child) for child in item.get("contents", []))
        return 0

    def _build_path_map(self, item, current_path=Path("/")):
        """Build a flat dictionary mapping paths to items for faster lookups."""
        normalized_path = self._sanitize_path(current_path)
        path_map = {normalized_path: item}
        if item["type"] == "directory":
            for child in item.get("contents", []):
                child_path = current_path / child["name"]
                path_map.update(self._build_path_map(child, child_path))
        return path_map

    def _sanitize_path(self, path):
        """Sanitize and normalize the path."""
        path_str = str(path)
        # Apply Unicode normalization if specified
        if self.unicode_normalization != "none":
            path_str = unicodedata.normalize(self.unicode_normalization, path_str)
        # Remove null bytes
        path_str = path_str.replace("\0", "")
        # Resolve path to prevent traversal attacks
        path_str = os.path.normpath("/" + path_str).lstrip("/")
        return path_str

    @lru_cache(maxsize=1000)
    def _get_item(self, path):
        """Get an item from the path map, with caching for performance."""
        normalized_path = self._sanitize_path(path)
        return self.path_map.get(normalized_path)

    def _generate_block_data(self, path, block):
        """
        Retrieve pre-generated data for a specific block of a file.
        """
        normalized_path = self._sanitize_path(path)
        combined = normalized_path + "\x01" + str(block)  # Use \x01 instead of \0 as separator
        hash_value = hashlib.md5(combined.encode("utf-8")).digest()
        cache_index = int.from_bytes(hash_value, byteorder="big") % self.pre_generated_blocks
        return self.block_cache[cache_index]

    def read(self, path, size, offset, fh):
        """
        Read data from a file in the virtual filesystem.
        """
        self.logger.debug(f"read called for path: {path}, size: {size}, offset: {offset}")

        item = self._get_item(path)
        if item is None or item["type"] != "file":
            self.logger.warning(f"Invalid file path: {path} {_unicode_to_named_entities(path)}")
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
            return bytes(data)

    def getattr(self, path, fh=None):
        """Get attributes of a file or directory."""
        self._increment_stats()
        self.logger.debug(f"getattr called for path: {path}")
        item = self._get_item(path)
        if item is None:
            self.logger.warning(
                f"Path not found (requested file is not in file system): {path} {_unicode_to_named_entities(path)}"
            )
            raise FuseOSError(ENOENT)

        st = {
            "st_atime": self.mtime,
            "st_ctime": self.mtime,
            "st_mtime": self.mtime,
            "st_nlink": 2,
            "st_uid": self.uid,
            "st_gid": self.gid,
        }

        if item["type"] == "directory":
            st["st_mode"] = S_IFDIR | 0o555
            st["st_size"] = 4096  # Standard size for directories
        else:
            st["st_mode"] = S_IFREG | 0o444
            st["st_size"] = item.get("size", 0)

        self.logger.debug(f"getattr returned: {st}")
        return st

    def readdir(self, path, fh):
        """Read the contents of a directory."""
        self._increment_stats()
        self.logger.debug(f"readdir called for path: {path}")
        item = self._get_item(path)
        if item is None or item["type"] != "directory":
            self.logger.warning(f"Invalid directory path: {path} {_unicode_to_named_entities(path)}")
            raise FuseOSError(ENOENT)

        yield "."
        yield ".."
        for child in item.get("contents", []):
            self.logger.debug(f"Yielding child: {child['name']}")
            yield child["name"]

    def statfs(self, path):
        """Get filesystem statistics."""
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
        """Check if a path is accessible."""
        if not self._get_item(path):
            raise FuseOSError(ENOENT)
        return 0

    def opendir(self, path):
        """Open a directory (basically just check if it exists)."""
        if not self._get_item(path):
            raise FuseOSError(ENOENT)
        return 0

    def releasedir(self, path, fh):
        """Called when a directory is closed."""
        return 0

    def open(self, path, flags):
        """Open a file (basically just check if it exists)."""
        if not self._get_item(path):
            raise FuseOSError(ENOENT)
        return 0

    def release(self, path, fh):
        """Called when a file is closed."""
        return 0

    def readlink(self, path):
        """Read a symlink (not supported in this filesystem)."""
        raise FuseOSError(ENOENT)

    def utimens(self, path, times=None):
        """Change file timestamps (no-op for read-only filesystem)."""
        return 0

    def chmod(self, path, mode):
        """Change file permissions (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def chown(self, path, uid, gid):
        """Change file owner (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def mknod(self, path, mode, dev):
        """Create a file node (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def mkdir(self, path, mode):
        """Create a directory (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def unlink(self, path):
        """Remove a file (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def rmdir(self, path):
        """Remove a directory (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def symlink(self, name, target):
        """Create a symbolic link (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def rename(self, old, new):
        """Rename a file or directory (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def link(self, target, name):
        """Create a hard link (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)

    def truncate(self, path, length):
        """Truncate a file (not allowed in read-only filesystem)."""
        raise FuseOSError(EROFS)


def main():
    """Main function to set up and run the FUSE filesystem."""
    parser = argparse.ArgumentParser(description="Mount a JSON file as a read-only filesystem")
    parser.add_argument("json_file", type=Path, help="Path to the JSON file describing the filesystem")
    parser.add_argument("mount_point", type=Path, help="Mount point for the filesystem")
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
        default="128K",
        help="Size of blocks for semi-random data generation (e.g., 1M, 2G, 512K). Default: 128K",
    )
    parser.add_argument(
        "--pre-generated-blocks",
        type=int,
        default=100,
        help="Number of pre-generated blocks to use for semi-random data generation. Default: 100",
    )
    parser.add_argument(
        "--seed",
        type=int,
        help="Seed for random number generation. If not provided, the random number 4 is used.",
    )
    parser.add_argument(
        "--no-macos-cache-files",
        action="store_true",
        help="Do not add macOS control files to prevent caching",
    )
    parser.add_argument(
        "--uid",
        type=int,
        default=os.getuid(),
        help="Set the UID for all files and directories (default: current user's UID)",
    )
    parser.add_argument(
        "--gid",
        type=int,
        default=os.getgid(),
        help="Set the GID for all files and directories (default: current user's GID)",
    )
    parser.add_argument(
        "--mtime",
        type=str,
        default="2017-10-17",
        help="Set the modification time for all files and directories (default: 2017-10-17)",
    )
    parser.add_argument(
        "--unicode-normalization",
        choices=["NFC", "NFD", "NFKC", "NFKD", "none"],
        default="NFD",
        help="Unicode normalization form to use (default: NFD, also supports NFC, NFKC, NFKD, or 'none' for no normalization) "
        "see https://www.unicode.org/faq/normalization.html for more information",
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

    mtime = datetime.datetime.strptime(args.mtime, "%Y-%m-%d").timestamp()

    with args.json_file.open("r") as f:
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
            pre_generated_blocks=args.pre_generated_blocks,
            seed=args.seed,
            add_macos_cache_files=not args.no_macos_cache_files,
            uid=args.uid,
            gid=args.gid,
            mtime=mtime,
            unicode_normalization=args.unicode_normalization,
        ),
        str(args.mount_point),
        nothreads=True,
        foreground=True,
    )


if __name__ == "__main__":
    main()
