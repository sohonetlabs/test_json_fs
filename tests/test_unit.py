"""Unit tests for JSONFileSystem without mounting."""

import json
import os
import sys
import pytest
from pathlib import Path

# Add parent directory to path to import jsonfs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jsonfs import JSONFileSystem, parse_size, humanize_bytes, _unicode_to_named_entities


class TestHelperFunctions:
    """Test standalone helper functions."""
    
    def test_parse_size(self):
        """Test size parsing functionality."""
        # Valid cases
        assert parse_size("100") == 100
        assert parse_size("1K") == 1024
        assert parse_size("1k") == 1024
        assert parse_size("2M") == 2 * 1024 * 1024
        assert parse_size("1G") == 1024 * 1024 * 1024
        assert parse_size(100) == 100
        
        # Additional valid cases
        assert parse_size("0") == 0
        assert parse_size("1B") == 1
        assert parse_size("1T") == 1024**4
        assert parse_size("1P") == 1024**5
        assert parse_size("1E") == 1024**6
        
    def test_parse_size_edge_cases(self):
        """Test parse_size edge cases and error conditions."""
        import pytest
        
        # Empty string should raise ValueError
        with pytest.raises(ValueError, match="Size cannot be empty"):
            parse_size("")
            
        # Whitespace-only string should raise ValueError
        with pytest.raises(ValueError, match="Size cannot be empty"):
            parse_size("   ")
            
        # Single unit character without number should raise ValueError
        with pytest.raises(ValueError, match="missing numeric part"):
            parse_size("K")
            
        # Invalid numeric part should raise ValueError
        with pytest.raises(ValueError, match="numeric part must be an integer"):
            parse_size("1.5K")
            
        # Invalid format should raise ValueError
        with pytest.raises(ValueError, match="must be an integer"):
            parse_size("abc")
            
        # Invalid unit should be treated as plain integer (should fail)
        with pytest.raises(ValueError, match="must be an integer"):
            parse_size("100X")
            
        # None should raise appropriate error
        with pytest.raises(ValueError):
            parse_size(None)
    
    def test_humanize_bytes(self):
        """Test human-readable byte formatting."""
        assert humanize_bytes(0) == "0.00 Bytes"
        assert humanize_bytes(1) == "1 byte"
        assert humanize_bytes(1024) == "1.00 KB"
        assert humanize_bytes(1024 * 1024) == "1.00 MB"
        assert humanize_bytes(1536) == "1.50 KB"
    
    def test_unicode_to_named_entities(self):
        """Test Unicode entity conversion."""
        assert _unicode_to_named_entities("hello") == "hello"
        assert "LATIN SMALL LETTER E WITH ACUTE" in _unicode_to_named_entities("café")
        assert "GRINNING FACE" in _unicode_to_named_entities("😀")


class TestConstructorValidation:
    """Test constructor parameter validation."""
    
    def get_valid_json_data(self):
        """Return valid JSON data for testing."""
        return [{"type": "directory", "name": "/", "contents": []}]
    
    def test_invalid_fill_char(self):
        """Test fill_char validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        # Empty string should fail
        with pytest.raises(ValueError, match="fill_char must be a single character"):
            JSONFileSystem(json_data, fill_char="")
        
        # Multiple characters should fail
        with pytest.raises(ValueError, match="fill_char must be a single character"):
            JSONFileSystem(json_data, fill_char="ab")
        
        # Non-string should fail
        with pytest.raises(ValueError, match="fill_char must be a single character"):
            JSONFileSystem(json_data, fill_char=123)
    
    def test_invalid_fill_mode(self):
        """Test fill_mode validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        with pytest.raises(ValueError, match="fill_mode must be"):
            JSONFileSystem(json_data, fill_mode="invalid_mode")
    
    def test_invalid_rate_limit(self):
        """Test rate_limit validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        # Negative should fail
        with pytest.raises(ValueError, match="rate_limit must be a non-negative number"):
            JSONFileSystem(json_data, rate_limit=-1)
        
        # Non-numeric should fail
        with pytest.raises(ValueError, match="rate_limit must be a non-negative number"):
            JSONFileSystem(json_data, rate_limit="invalid")
    
    def test_invalid_block_size(self):
        """Test block_size validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        # Zero should fail
        with pytest.raises(ValueError, match="block_size must be a positive integer"):
            JSONFileSystem(json_data, block_size=0)
        
        # Negative should fail
        with pytest.raises(ValueError, match="block_size must be a positive integer"):
            JSONFileSystem(json_data, block_size=-1)
        
        # Non-integer should fail
        with pytest.raises(ValueError, match="block_size must be a positive integer"):
            JSONFileSystem(json_data, block_size=1.5)
    
    def test_invalid_pre_generated_blocks(self):
        """Test pre_generated_blocks validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        # Zero should fail
        with pytest.raises(ValueError, match="pre_generated_blocks must be a positive integer"):
            JSONFileSystem(json_data, pre_generated_blocks=0)
        
        # Negative should fail
        with pytest.raises(ValueError, match="pre_generated_blocks must be a positive integer"):
            JSONFileSystem(json_data, pre_generated_blocks=-1)
    
    def test_invalid_seed(self):
        """Test seed validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        # Non-integer should fail (None is allowed)
        with pytest.raises(ValueError, match="seed must be an integer or None"):
            JSONFileSystem(json_data, seed="invalid")
    
    def test_invalid_unicode_normalization(self):
        """Test unicode_normalization validation."""
        import pytest
        json_data = self.get_valid_json_data()
        
        with pytest.raises(ValueError, match="unicode_normalization must be one of"):
            JSONFileSystem(json_data, unicode_normalization="invalid")
    
    def test_valid_parameters(self):
        """Test that valid parameters work correctly."""
        json_data = self.get_valid_json_data()
        
        # Should not raise any exceptions
        fs = JSONFileSystem(
            json_data,
            fill_char="X",
            fill_mode="fill_char",
            rate_limit=0.1,
            iop_limit=100,
            block_size=1024,
            pre_generated_blocks=10,
            seed=42,
            unicode_normalization="NFC"
        )
        
        assert fs.fill_char == "X"
        assert fs.rate_limit == 0.1


class TestJSONFileSystem:
    """Test JSONFileSystem class methods."""
    
    @pytest.fixture
    def simple_fs(self):
        """Create a simple filesystem for testing."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100},
                    {
                        "type": "directory",
                        "name": "subdir",
                        "contents": [
                            {"type": "file", "name": "nested.txt", "size": 50}
                        ]
                    }
                ]
            }
        ]
        return JSONFileSystem(json_data, report=False, pre_generated_blocks=10, block_size=1024)
    
    def test_initialization(self, simple_fs):
        """Test filesystem initialization."""
        assert simple_fs.total_files == 2
        assert simple_fs.total_size == 150
        assert simple_fs.root["type"] == "directory"
    
    def test_sanitize_path(self, simple_fs):
        """Test path sanitization."""
        assert simple_fs._sanitize_path("/test.txt") == "test.txt"
        assert simple_fs._sanitize_path("test.txt") == "test.txt"
        assert simple_fs._sanitize_path("/subdir/nested.txt") == "subdir/nested.txt"
        assert simple_fs._sanitize_path("//test.txt") == "test.txt"
        assert simple_fs._sanitize_path("/./test.txt") == "test.txt"
        assert simple_fs._sanitize_path("/subdir/../test.txt") == "test.txt"
        
        # Test null byte removal
        assert simple_fs._sanitize_path("/test\x00.txt") == "test.txt"
    
    def test_get_item(self, simple_fs):
        """Test item retrieval."""
        # Test root
        root = simple_fs._get_item("/")
        assert root is not None
        assert root["type"] == "directory"
        
        # Test file
        file_item = simple_fs._get_item("/test.txt")
        assert file_item is not None
        assert file_item["type"] == "file"
        assert file_item["size"] == 100
        
        # Test nested file
        nested = simple_fs._get_item("/subdir/nested.txt")
        assert nested is not None
        assert nested["type"] == "file"
        assert nested["size"] == 50
        
        # Test non-existent
        assert simple_fs._get_item("/nonexistent.txt") is None
    
    def test_fill_buffer_caching(self, simple_fs):
        """Test fill buffer generation and caching."""
        # First call should cache
        buffer1 = simple_fs._get_fill_buffer(100)
        assert len(buffer1) == 100
        assert buffer1 == b'\x00' * 100
        
        # Second call should use cache
        buffer2 = simple_fs._get_fill_buffer(100)
        assert buffer1 is buffer2  # Same object from cache
        
        # Different size
        buffer3 = simple_fs._get_fill_buffer(50)
        assert len(buffer3) == 50
    
    def test_path_sanitization_caching(self, simple_fs):
        """Test that path sanitization is cached."""
        # Clear cache first
        simple_fs._sanitize_path.cache_clear()
        
        # First call
        path1 = simple_fs._sanitize_path("/test.txt")
        info1 = simple_fs._sanitize_path.cache_info()
        
        # Second call should hit cache
        path2 = simple_fs._sanitize_path("/test.txt")
        info2 = simple_fs._sanitize_path.cache_info()
        
        assert path1 == path2
        assert info2.hits == info1.hits + 1
    
    def test_calculate_total_size(self):
        """Test size calculation."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "file1.txt", "size": 100},
                    {"type": "file", "name": "file2.txt", "size": 200},
                    {
                        "type": "directory",
                        "name": "subdir",
                        "contents": [
                            {"type": "file", "name": "file3.txt", "size": 300}
                        ]
                    }
                ]
            }
        ]
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=10, block_size=1024)
        assert fs.total_size == 600
    
    def test_count_files(self):
        """Test file counting."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "file1.txt", "size": 100},
                    {"type": "file", "name": "file2.txt", "size": 200},
                    {
                        "type": "directory",
                        "name": "subdir",
                        "contents": [
                            {"type": "file", "name": "file3.txt", "size": 300},
                            {"type": "file", "name": "file4.txt", "size": 400}
                        ]
                    }
                ]
            }
        ]
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=10, block_size=1024)
        assert fs.total_files == 4


class TestJSONValidation:
    """Test JSON structure validation."""
    
    def test_empty_json(self):
        """Test handling of empty JSON."""
        with pytest.raises(ValueError, match="at least one item"):
            JSONFileSystem([])
    
    def test_invalid_root_type(self):
        """Test handling of non-directory root."""
        json_data = [{"type": "file", "name": "test.txt", "size": 100}]
        with pytest.raises(ValueError, match="must be a directory"):
            JSONFileSystem(json_data)
    
    def test_missing_root_name(self):
        """Test handling of missing root name."""
        json_data = [{"type": "directory", "contents": []}]
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=10, block_size=1024)
        assert fs.root["name"] == "/"
    
    def test_missing_root_contents(self):
        """Test handling of missing root contents."""
        json_data = [{"type": "directory", "name": "/"}]
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=10, block_size=1024, add_macos_cache_files=False)
        assert fs.root["contents"] == []


class TestUnicodeHandling:
    """Test Unicode filename handling."""
    
    def test_unicode_filenames(self):
        """Test handling of Unicode filenames."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "café.txt", "size": 100},
                    {"type": "file", "name": "文件.txt", "size": 200}
                ]
            }
        ]
        fs = JSONFileSystem(json_data, unicode_normalization="NFD", report=False, pre_generated_blocks=10, block_size=1024)
        
        # Should find files with normalized paths
        assert fs._get_item("/café.txt") is not None
        assert fs._get_item("/文件.txt") is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])