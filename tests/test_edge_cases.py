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
    
    def test_iop_window_reset(self):
        """Test IOP limiting window reset after 1 second."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        # Create filesystem with 5 IOPS limit
        fs = JSONFileSystem(json_data, iop_limit=5, report=False, pre_generated_blocks=1, block_size=1024)
        
        import time
        
        # Mock time to control window reset
        original_time = time.time
        current_mock_time = original_time()
        
        def mock_time():
            return current_mock_time
        
        with patch('time.time', mock_time):
            # Initialize the window
            fs._apply_iop_limit()
            
            # Do 5 operations in the first window
            for i in range(4):
                fs._apply_iop_limit()
            
            # Now we've done 5 operations, window should be at limit
            assert fs.iop_window_count == 5
            
            # Advance time by 1.1 seconds to trigger window reset
            current_mock_time += 1.1
            
            # This operation should reset the window
            fs._apply_iop_limit()
            
            # Window should be reset with count = 1
            assert fs.iop_window_count == 1
            assert fs.iop_window_start == current_mock_time


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
                    {"type": "directory", "name": "dir", "contents": []},
                    {"type": "file", "name": "test.txt", "size": 50}
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
    
    def test_access_operations(self, fs):
        """Test access, open, and release operations."""
        # Test access on existing file
        assert fs.access("/file.txt", 0) == 0
        
        # Test access on non-existent file
        from fuse import FuseOSError
        from errno import ENOENT
        with pytest.raises(FuseOSError) as exc:
            fs.access("/nonexistent", 0)
        assert exc.value.errno == ENOENT
        
        # Test open
        assert fs.open("/file.txt", 0) == 0
        
        # Test open non-existent
        with pytest.raises(FuseOSError) as exc:
            fs.open("/nonexistent", 0)
        assert exc.value.errno == ENOENT
        
        # Test release (should always succeed)
        assert fs.release("/file.txt", 0) == 0
    
    def test_directory_operations(self, fs):
        """Test opendir and releasedir operations."""
        # Test opendir on existing directory
        assert fs.opendir("/dir") == 0
        
        # Test opendir on non-existent
        from fuse import FuseOSError
        from errno import ENOENT
        with pytest.raises(FuseOSError) as exc:
            fs.opendir("/nonexistent")
        assert exc.value.errno == ENOENT
        
        # Test releasedir (should always succeed)
        assert fs.releasedir("/dir", 0) == 0
    
    def test_statfs_operation(self, fs):
        """Test statfs operation."""
        stats = fs.statfs("/")
        
        # Check required fields
        assert "f_bsize" in stats
        assert "f_blocks" in stats
        assert "f_files" in stats
        assert stats["f_bsize"] == 4096
        assert stats["f_files"] == 2  # Two files in our test fs
    
    def test_symlink_operations(self, fs):
        """Test symlink-related operations."""
        from fuse import FuseOSError
        from errno import ENOENT, EROFS
        
        # readlink should fail (not supported)
        with pytest.raises(FuseOSError) as exc:
            fs.readlink("/any")
        assert exc.value.errno == ENOENT
        
        # symlink creation should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.symlink("target", "link")
        assert exc.value.errno == EROFS
    
    def test_timestamp_operations(self, fs):
        """Test timestamp-related operations."""
        # utimens should succeed (no-op for read-only fs)
        assert fs.utimens("/file.txt", None) == 0
        assert fs.utimens("/file.txt", (1234567890, 1234567890)) == 0
    
    def test_permission_operations(self, fs):
        """Test permission-related operations."""
        from fuse import FuseOSError
        from errno import EROFS
        
        # chmod should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.chmod("/file.txt", 0o755)
        assert exc.value.errno == EROFS
        
        # chown should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.chown("/file.txt", 1000, 1000)
        assert exc.value.errno == EROFS
    
    def test_link_operations(self, fs):
        """Test hard link operations."""
        from fuse import FuseOSError
        from errno import EROFS
        
        # link should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.link("/file.txt", "/hardlink")
        assert exc.value.errno == EROFS
        
        # truncate should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.truncate("/file.txt", 50)
        assert exc.value.errno == EROFS
    
    def test_xattr_operations(self, fs):
        """Test extended attribute operations."""
        from fuse import FuseOSError
        from errno import ENODATA, EROFS
        
        # getxattr should return ENODATA
        with pytest.raises(FuseOSError) as exc:
            fs.getxattr("/file.txt", "user.test")
        assert exc.value.errno == ENODATA
        
        # listxattr should return empty list
        assert fs.listxattr("/file.txt") == []
        
        # setxattr should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.setxattr("/file.txt", "user.test", b"value", 0)
        assert exc.value.errno == EROFS
    
    def test_rename_operation(self, fs):
        """Test rename operation."""
        from fuse import FuseOSError
        from errno import EROFS
        
        # rename should fail (read-only)
        with pytest.raises(FuseOSError) as exc:
            fs.rename("/file.txt", "/newname.txt")
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


class TestUncoveredErrorCases:
    """Test uncovered error cases in FUSE operations."""
    
    @pytest.fixture
    def fs(self):
        """Create a test filesystem."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "file.txt", "size": 100},
                    {"type": "directory", "name": "subdir", "contents": []}
                ]
            }
        ]
        return JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
    
    def test_calculate_size_unknown_type(self, fs):
        """Test _calculate_total_size with unknown item type."""
        # Create item with unknown type
        unknown_item = {"name": "unknown", "type": "unknown_type", "size": 100}
        
        # This should trigger the warning for unknown item type
        with patch.object(fs.logger, 'warning') as mock_warning:
            size = fs._calculate_total_size(unknown_item)
            assert size == 0
            mock_warning.assert_called_once_with("Unknown item type: unknown_type for unknown")
            
    def test_count_files_missing_type(self, fs):
        """Test _count_files with missing type field."""
        # Item without type field
        item_no_type = {"name": "test"}
        count = fs._count_files(item_no_type)
        assert count == 0
        
    def test_count_files_unknown_type(self, fs):
        """Test _count_files with unknown type."""
        # Item with unknown type
        item_unknown = {"name": "test", "type": "symlink"}
        count = fs._count_files(item_unknown)
        assert count == 0
        
    def test_read_invalid_file_path(self, fs):
        """Test reading from a directory path."""
        # Try to read a directory as a file
        with patch.object(fs.logger, 'warning') as mock_warning:
            from fuse import FuseOSError
            from errno import ENOENT
            with pytest.raises(FuseOSError) as cm:
                fs.read("/subdir", 0, 0, None)
            assert cm.value.errno == ENOENT
            mock_warning.assert_called_once()
            assert "Invalid file path" in str(mock_warning.call_args)
            
    def test_readdir_on_file(self, fs):
        """Test readdir on a file path."""
        # Try to list a file as directory
        with patch.object(fs.logger, 'warning') as mock_warning:
            from fuse import FuseOSError
            from errno import ENOENT
            with pytest.raises(FuseOSError) as cm:
                list(fs.readdir("/file.txt", None))
            assert cm.value.errno == ENOENT
            mock_warning.assert_called_once()
            assert "Invalid directory path" in str(mock_warning.call_args)
            
    def test_getattr_appledouble_file(self):
        """Test getattr on AppleDouble file with ignore flag."""
        json_data = [{"name": "/", "type": "directory", "contents": []}]
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
        fs.ignore_appledouble = True
        
        # Try to access AppleDouble file
        with patch.object(fs.logger, 'debug') as mock_debug:
            from fuse import FuseOSError
            from errno import ENOENT
            with pytest.raises(FuseOSError) as cm:
                fs.getattr("/._test.txt")
            assert cm.value.errno == ENOENT
            mock_debug.assert_called_with("Ignoring AppleDouble file: /._test.txt")
            
    def test_print_structure_max_depth(self):
        """Test _print_structure with deep nesting."""
        json_data = [{
            "name": "/", 
            "type": "directory",
            "contents": [
                {
                    "name": "level1",
                    "type": "directory", 
                    "contents": [
                        {
                            "name": "level2",
                            "type": "directory",
                            "contents": [
                                {
                                    "name": "level3",
                                    "type": "directory",
                                    "contents": []
                                }
                            ]
                        }
                    ]
                }
            ]
        }]
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
        
        # Mock logger.debug to capture calls
        debug_calls = []
        with patch.object(fs.logger, 'debug', side_effect=lambda msg: debug_calls.append(msg)):
            # This should stop at max_depth=2
            fs._print_structure(json_data[0], depth=0, max_depth=2)
            
            # Join all debug messages
            output = '\n'.join(debug_calls)
            
            # Should contain level1 and level2 but not level3
            assert "level1" in output
            assert "level2" in output
            assert "level3" not in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])