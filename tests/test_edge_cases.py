"""Edge case and error handling tests for JSONFileSystem."""

import json
import os
import sys
import pytest
import subprocess
import tempfile
from unittest.mock import patch, MagicMock

# Add parent directory to path to import jsonfs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jsonfs import JSONFileSystem, FILL_CHAR_MODE, SEMI_RANDOM_MODE


class TestFillCharValidation:
    """Test fill character validation."""
    
    def test_fill_char_validation_in_main(self):
        """Test that multi-character fill-char is rejected."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{"type": "directory", "name": "/", "contents": []}], f)
            f.flush()
            
            # Test multi-character fill-char
            result = subprocess.run([
                sys.executable, "jsonfs.py", 
                f.name, "/tmp/test",
                "--fill-char", "ab"
            ], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            
            assert result.returncode != 0
            assert "must be exactly one character" in result.stderr
            
            os.unlink(f.name)
    
    def test_single_char_accepted(self):
        """Test that single character fill-char is accepted."""
        json_data = [{"type": "directory", "name": "/", "contents": []}]
        fs = JSONFileSystem(json_data, fill_char="X", report=False, pre_generated_blocks=1, block_size=1024)
        assert fs.fill_char == "X"


class TestSemiRandomMode:
    """Test semi-random data generation."""
    
    def test_semi_random_deterministic(self):
        """Test that semi-random mode is deterministic with same seed."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 1000}
                ]
            }
        ]
        
        # Create two filesystems with same seed
        fs1 = JSONFileSystem(json_data, fill_mode=SEMI_RANDOM_MODE, seed=42, report=False, pre_generated_blocks=10, block_size=512)
        fs2 = JSONFileSystem(json_data, fill_mode=SEMI_RANDOM_MODE, seed=42, report=False, pre_generated_blocks=10, block_size=512)
        
        # Read same file from both
        data1 = fs1.read("/test.txt", 100, 0, None)
        data2 = fs2.read("/test.txt", 100, 0, None)
        
        assert data1 == data2
    
    def test_semi_random_different_seeds(self):
        """Test that different seeds produce different data."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 1000}
                ]
            }
        ]
        
        fs1 = JSONFileSystem(json_data, fill_mode=SEMI_RANDOM_MODE, seed=42, report=False, pre_generated_blocks=10, block_size=512)
        fs2 = JSONFileSystem(json_data, fill_mode=SEMI_RANDOM_MODE, seed=123, report=False, pre_generated_blocks=10, block_size=512)
        
        data1 = fs1.read("/test.txt", 100, 0, None)
        data2 = fs2.read("/test.txt", 100, 0, None)
        
        assert data1 != data2
    
    def test_block_boundary_handling(self):
        """Test reading across block boundaries."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 2048}
                ]
            }
        ]
        
        fs = JSONFileSystem(json_data, fill_mode=SEMI_RANDOM_MODE, seed=42, report=False, 
                           pre_generated_blocks=10, block_size=512)
        
        # Read across block boundary (block 0 ends at 512, block 1 starts at 512)
        data = fs.read("/test.txt", 100, 462, None)  # Read 100 bytes starting at offset 462
        assert len(data) == 100
        
        # Verify continuity by reading in two parts
        part1 = fs.read("/test.txt", 50, 462, None)
        part2 = fs.read("/test.txt", 50, 512, None)
        assert data == part1 + part2


class TestLargeFiles:
    """Test handling of large files."""
    
    def test_32bit_boundary_files(self):
        """Test files at 32-bit boundaries."""
        test_sizes = [
            2**31 - 1,  # Just under 2GB
            2**31,      # Exactly 2GB
            2**32 - 1,  # Just under 4GB
            2**32,      # Exactly 4GB
        ]
        
        for size in test_sizes:
            json_data = [
                {
                    "type": "directory",
                    "name": "/",
                    "contents": [
                        {"type": "file", "name": "large.bin", "size": size}
                    ]
                }
            ]
            
            fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
            assert fs.total_size == size
            
            # Test reading at various offsets
            if size > 1000:
                data = fs.read("/large.bin", 100, size - 100, None)
                assert len(data) == 100


class TestRateLimiting:
    """Test rate limiting functionality."""
    
    def test_rate_limiting(self):
        """Test that rate limiting delays operations."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        # Create filesystem with 0.1 second rate limit
        fs = JSONFileSystem(json_data, rate_limit=0.1, report=False, pre_generated_blocks=1, block_size=1024)
        
        import time
        
        # Perform two operations and measure time
        start = time.time()
        fs.getattr("/test.txt")
        fs.getattr("/test.txt")
        elapsed = time.time() - start
        
        # Should take at least 0.1 seconds due to rate limiting
        assert elapsed >= 0.1


class TestIOPLimiting:
    """Test IOP limiting functionality."""
    
    def test_iop_limiting(self):
        """Test that IOP limiting restricts operations per second."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        # Create filesystem with 10 IOPS limit
        fs = JSONFileSystem(json_data, iop_limit=10, report=False, pre_generated_blocks=1, block_size=1024)
        
        import time
        
        # Try to perform 15 operations in quick succession
        start = time.time()
        for i in range(15):
            fs.getattr("/test.txt")
        elapsed = time.time() - start
        
        # Should take at least 1 second to complete 15 ops with 10 IOPS limit
        # Allow small timing variance
        assert elapsed >= 0.9


class TestSpecialCharacters:
    """Test handling of special characters in filenames."""
    
    def test_null_byte_in_path(self):
        """Test that null bytes are stripped from paths."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
        
        # Path with null byte should still find the file
        assert fs._get_item("/test\x00.txt") is not None
        assert fs._sanitize_path("/test\x00.txt") == "test.txt"
    
    def test_unicode_normalization_options(self):
        """Test different Unicode normalization options."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "café.txt", "size": 100}
                ]
            }
        ]
        
        # Test with different normalization forms
        for norm in ["NFC", "NFD", "NFKC", "NFKD", "none"]:
            fs = JSONFileSystem(json_data, unicode_normalization=norm, report=False, 
                               pre_generated_blocks=1, block_size=1024)
            # Should be able to create filesystem with any normalization option
            assert fs.unicode_normalization == norm


class TestFUSEOperations:
    """Test FUSE operation methods."""
    
    @pytest.fixture
    def fs(self):
        """Create a test filesystem."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "file.txt", "size": 100},
                    {"type": "directory", "name": "dir", "contents": []}
                ]
            }
        ]
        return JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
    
    def test_getattr_file(self, fs):
        """Test getattr on a file."""
        attr = fs.getattr("/file.txt")
        assert attr["st_size"] == 100
        assert attr["st_mode"] & 0o100000  # Is regular file
    
    def test_getattr_directory(self, fs):
        """Test getattr on a directory."""
        attr = fs.getattr("/dir")
        assert attr["st_mode"] & 0o40000  # Is directory
    
    def test_getattr_nonexistent(self, fs):
        """Test getattr on non-existent path."""
        from fuse import FuseOSError
        from errno import ENOENT
        
        with pytest.raises(FuseOSError) as exc:
            fs.getattr("/nonexistent")
        assert exc.value.errno == ENOENT
    
    def test_readdir(self, fs):
        """Test reading directory contents."""
        contents = list(fs.readdir("/", None))
        assert "." in contents
        assert ".." in contents
        assert "file.txt" in contents
        assert "dir" in contents
    
    def test_read_operations(self, fs):
        """Test various read operations."""
        # Read entire file
        data = fs.read("/file.txt", 100, 0, None)
        assert len(data) == 100
        
        # Read partial file
        data = fs.read("/file.txt", 50, 0, None)
        assert len(data) == 50
        
        # Read with offset
        data = fs.read("/file.txt", 50, 50, None)
        assert len(data) == 50
        
        # Read beyond file size
        data = fs.read("/file.txt", 50, 80, None)
        assert len(data) == 20  # Only 20 bytes left
    
    def test_write_operations_fail(self, fs):
        """Test that write operations fail with EROFS."""
        from fuse import FuseOSError
        from errno import EROFS
        
        # Test various write operations
        with pytest.raises(FuseOSError) as exc:
            fs.mkdir("/newdir", 0o755)
        assert exc.value.errno == EROFS
        
        with pytest.raises(FuseOSError) as exc:
            fs.mknod("/newfile", 0o644, 0)
        assert exc.value.errno == EROFS
        
        with pytest.raises(FuseOSError) as exc:
            fs.unlink("/file.txt")
        assert exc.value.errno == EROFS
        
        with pytest.raises(FuseOSError) as exc:
            fs.rmdir("/dir")
        assert exc.value.errno == EROFS


class TestCacheLimits:
    """Test cache size limits and eviction."""
    
    def test_path_cache_limit(self):
        """Test that path cache respects size limit."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": []
            }
        ]
        
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
        
        # Clear cache
        fs._sanitize_path.cache_clear()
        
        # Add many paths to exceed cache limit (1000)
        for i in range(1500):
            fs._sanitize_path(f"/path_{i}")
        
        # Cache should not exceed maxsize
        cache_info = fs._sanitize_path.cache_info()
        assert cache_info.currsize <= 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])