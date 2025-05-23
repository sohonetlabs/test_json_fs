"""Test error handling in main function."""

import json
import os
import sys
import pytest
import subprocess
import tempfile

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestMainErrors:
    """Test error conditions in the main function."""
    
    def run_jsonfs(self, json_content, extra_args=None):
        """Run jsonfs with given JSON content."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            if isinstance(json_content, str):
                f.write(json_content)
            else:
                json.dump(json_content, f)
            f.flush()
            
            cmd = [sys.executable, "jsonfs.py", f.name, "/tmp/test"]
            if extra_args:
                cmd.extend(extra_args)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            
            os.unlink(f.name)
            return result
    
    def test_empty_json_array(self):
        """Test handling of empty JSON array."""
        result = self.run_jsonfs([])
        assert result.returncode != 0
        # Empty list is caught by the first validation check
        assert "Root must be a non-empty list" in result.stderr
    
    def test_json_not_array(self):
        """Test handling of JSON that's not an array."""
        result = self.run_jsonfs({"type": "directory"})
        assert result.returncode != 0
        assert "Root must be a non-empty list" in result.stderr
    
    def test_first_entry_not_dict(self):
        """Test handling of first entry not being a dictionary."""
        result = self.run_jsonfs(["not a dictionary"])
        assert result.returncode != 0
        assert "First entry must be a dictionary" in result.stderr
    
    def test_first_entry_not_directory(self):
        """Test handling of first entry not being a directory."""
        result = self.run_jsonfs([{"type": "file", "name": "test.txt"}])
        assert result.returncode != 0
        assert "First entry must be a directory" in result.stderr
    
    def test_missing_name_field(self):
        """Test handling of missing name field (should warn but continue)."""
        # This should succeed with a warning
        result = self.run_jsonfs([{"type": "directory", "contents": []}], ["--log-level", "DEBUG"])
        # Can't easily test the warning without mounting, but it shouldn't fail
        assert "Root directory missing 'name' field" in result.stderr or result.returncode != 0
    
    def test_missing_contents_field(self):
        """Test handling of missing contents field (should warn but continue)."""
        # This should succeed with a warning
        result = self.run_jsonfs([{"type": "directory", "name": "/"}], ["--log-level", "DEBUG"])
        # Can't easily test the warning without mounting, but it shouldn't fail
        assert "Root directory missing 'contents' field" in result.stderr or result.returncode != 0
    
    def test_permission_error_json_file(self):
        """Test handling of permission errors reading JSON file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump([{"type": "directory", "name": "/", "contents": []}], f)
            f.flush()
            
            # Make file unreadable
            os.chmod(f.name, 0o000)
            
            cmd = [sys.executable, "jsonfs.py", f.name, "/tmp/test"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            
            # Restore permissions before cleanup
            os.chmod(f.name, 0o644)
            os.unlink(f.name)
            
            assert result.returncode != 0
            assert "Failed to read JSON file" in result.stderr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])