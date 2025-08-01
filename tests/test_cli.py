"""Command-line interface tests for JSONFileSystem."""

import json
import os
import sys
import pytest
import subprocess
import tempfile

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCLI:
    """Test command-line interface."""
    
    @pytest.fixture
    def json_file(self):
        """Create a temporary JSON file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json_data = [
                {
                    "type": "directory",
                    "name": "/",
                    "contents": [
                        {"type": "file", "name": "test.txt", "size": 100}
                    ]
                }
            ]
            json.dump(json_data, f)
            f.flush()
            yield f.name
        os.unlink(f.name)
    
    def run_jsonfs(self, args, json_file=None):
        """Run jsonfs with given arguments."""
        cmd = [sys.executable, "jsonfs.py"]
        if json_file:
            cmd.extend([json_file, "/tmp/test"])
        cmd.extend(args)
        
        return subprocess.run(
            cmd, 
            capture_output=True, 
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    
    def test_help(self):
        """Test help output."""
        result = self.run_jsonfs(["--help"])
        assert result.returncode == 0
        assert "Mount a JSON file as a read-only filesystem" in result.stdout
        assert "--fill-char" in result.stdout
        assert "--semi-random" in result.stdout
    
    def test_version(self):
        """Test version output."""
        result = self.run_jsonfs(["--version"])
        assert result.returncode == 0
        assert "jsonfs.py" in result.stdout
        assert "1.6.7" in result.stdout
    
    def test_mutually_exclusive_fill_modes(self, json_file):
        """Test that fill-char and semi-random are mutually exclusive."""
        result = self.run_jsonfs(["--fill-char", "X", "--semi-random"], json_file)
        assert result.returncode != 0
        # argparse handles this automatically with mutually_exclusive_group
        assert "not allowed with argument" in result.stderr
    
    def test_invalid_json_file(self):
        """Test handling of invalid JSON file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("invalid json {")
            f.flush()
            
            result = self.run_jsonfs([], f.name)
            assert result.returncode != 0
            assert "Failed to parse JSON file" in result.stderr
            
            os.unlink(f.name)
    
    def test_invalid_date_format(self, json_file):
        """Test handling of invalid date format."""
        result = self.run_jsonfs(["--mtime", "invalid-date"], json_file)
        assert result.returncode != 0
        assert "Invalid date format" in result.stderr
        assert "Expected format: YYYY-MM-DD" in result.stderr
    
    def test_log_levels(self, json_file):
        """Test different log levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            result = self.run_jsonfs(["--log-level", level, "--help"])
            assert result.returncode == 0
    
    def test_block_size_parsing(self, json_file):
        """Test block size parsing."""
        valid_sizes = ["1K", "2M", "512K", "1G"]
        for size in valid_sizes:
            # Just test parsing, don't actually mount
            result = self.run_jsonfs(["--block-size", size, "--help"])
            assert result.returncode == 0
    
    def test_numeric_arguments(self, json_file):
        """Test numeric argument validation."""
        # Test rate limit
        result = self.run_jsonfs(["--rate-limit", "0.5", "--help"])
        assert result.returncode == 0
        
        # Test IOP limit
        result = self.run_jsonfs(["--iop-limit", "100", "--help"])
        assert result.returncode == 0
        
        # Test pre-generated blocks
        result = self.run_jsonfs(["--pre-generated-blocks", "50", "--help"])
        assert result.returncode == 0
        
        # Test seed
        result = self.run_jsonfs(["--seed", "12345", "--help"])
        assert result.returncode == 0
    
    def test_uid_gid_arguments(self, json_file):
        """Test UID/GID arguments."""
        result = self.run_jsonfs(["--uid", "1000", "--gid", "1000", "--help"])
        assert result.returncode == 0
    
    def test_unicode_normalization_options(self, json_file):
        """Test Unicode normalization options."""
        for norm in ["NFC", "NFD", "NFKC", "NFKD", "none"]:
            result = self.run_jsonfs(["--unicode-normalization", norm, "--help"])
            assert result.returncode == 0
    
    def test_missing_required_arguments(self):
        """Test that missing required arguments are caught."""
        result = self.run_jsonfs([])
        assert result.returncode != 0
        assert "required" in result.stderr
    
    def test_non_existent_json_file(self):
        """Test handling of non-existent JSON file."""
        result = self.run_jsonfs([], "/non/existent/file.json")
        assert result.returncode != 0
        assert "Failed to read JSON file" in result.stderr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])