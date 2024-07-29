import argparse
import json
import os
import threading
import time
from errno import ENOENT, EROFS
from functools import lru_cache, wraps
from stat import S_IFDIR, S_IFREG

from fuse import FUSE, FuseOSError, Operations


def humanize_bytes(bytes, precision=2):
    abbrevs = (
        (1 << 50, "PB"),
        (1 << 40, "TB"),
        (1 << 30, "GB"),
        (1 << 20, "MB"),
        (1 << 10, "KB"),
        (1, "bytes"),
    )
    if bytes == 1:
        return "1 byte"
    for factor, suffix in abbrevs:
        if bytes >= factor:
            break
    return f"{bytes / factor:.{precision}f} {suffix}"


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
        debug=False,
        fill_char="\0",
        rate_limit=0,
        iop_limit=0,
        report=True,
    ):
        self.json_data = json_data
        self.root = json_data[0]  # The first item should be the root directory
        self.now = time.time()
        self.debug = debug
        self.fill_char = fill_char
        self.rate_limit = rate_limit
        self.iop_limit = iop_limit
        self.report = report
        self.token_bucket = TokenBucket(iop_limit) if iop_limit > 0 else None

        # IOPS and data transfer counters
        self.iops_count = 0
        self.bytes_read = 0
        self.stats_lock = threading.Lock()

        # Pre-generate read buffers
        self.read_buffers = {
            4096: fill_char.encode() * 4096,
            8192: fill_char.encode() * 8192,
            16384: fill_char.encode() * 16384,
        }

        if self.debug:
            print("Root structure:")
            self._print_structure(self.root, max_depth=2)

        self.total_size = self._calculate_total_size(self.root)
        self.total_files = self._count_files(self.root)
        if self.debug:
            print(
                f"Total size: {humanize_bytes(self.total_size)} ({self.total_size} bytes)"
            )
            print(f"Total files: {self.total_files}")

        # Build flat dictionary for faster lookups
        self.path_map = self._build_path_map(self.root)

        # Start stats reporting thread
        if self.report:
            self.stats_thread = threading.Thread(target=self._report_stats, daemon=True)
            self.stats_thread.start()

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
        print(f"{indent}{item_name} ({item_type}, size: {size_str})")
        if item_type == "directory" and "contents" in item:
            for child in item["contents"][:5]:  # Print only first 5 children
                self._print_structure(child, depth + 1, max_depth)
            if len(item["contents"]) > 5:
                print(f"{indent}  ... ({len(item['contents']) - 5} more items)")

    def _calculate_total_size(self, item):
        item_type = item.get("type")
        item_name = item.get("name", "unnamed")
        if item_type == "file":
            size = item.get("size", 0)
            if self.debug:
                print(f"File: {item_name}, Size: {humanize_bytes(size)} ({size} bytes)")
            return size
        elif item_type == "directory":
            dir_size = sum(
                self._calculate_total_size(child) for child in item.get("contents", [])
            )
            if self.debug:
                print(
                    f"Directory: {item_name}, Size: {humanize_bytes(dir_size)} ({dir_size} bytes)"
                )
            return dir_size
        else:
            if self.debug:
                print(f"Unknown item type: {item_type} for {item_name}")
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

    @rate_limited
    def getattr(self, path, fh=None):
        self._increment_stats()
        item = self._get_item(path)
        if item is None:
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

        return st

    @rate_limited
    def readdir(self, path, fh):
        self._increment_stats()
        item = self._get_item(path)
        if item is None or item["type"] != "directory":
            raise FuseOSError(ENOENT)

        yield "."
        yield ".."
        for child in item.get("contents", []):
            yield child["name"]

    @rate_limited
    def read(self, path, size, offset, fh):
        item = self._get_item(path)
        if item is None or item["type"] != "file":
            raise FuseOSError(ENOENT)

        read_size = min(size, item.get("size", 0) - offset)
        self._increment_stats(read_size)
        if read_size in self.read_buffers:
            return self.read_buffers[read_size]
        return self.fill_char.encode() * read_size

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
    parser = argparse.ArgumentParser(
        description="Mount a JSON file as a read-only filesystem"
    )
    parser.add_argument(
        "json_file", help="Path to the JSON file describing the filesystem"
    )
    parser.add_argument("mount_point", help="Mount point for the filesystem")
    parser.add_argument("--debug", action="store_true", help="Enable debug output")
    parser.add_argument(
        "--fill-char",
        default="\0",
        help="Character to fill read data with (default: null byte)",
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
    args = parser.parse_args()

    with open(args.json_file, "r") as f:
        json_data = json.load(f)

    FUSE(
        JSONFileSystem(
            json_data,
            debug=args.debug,
            fill_char=args.fill_char,
            rate_limit=args.rate_limit,
            iop_limit=args.iop_limit,
            report=not args.report_stats,
        ),
        args.mount_point,
        nothreads=True,
        foreground=True,
    )


if __name__ == "__main__":
    main()
