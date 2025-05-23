"""Integration tests for JSONFileSystem with actual mounting."""

import json
import os
import sys
import tempfile
import time
import subprocess
import pytest
from pathlib import Path
import threading

# Add parent directory to path to import jsonfs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Skip all tests in this file if not on macOS or if FUSE is not available
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not os.path.exists("/usr/local/lib/libfuse-t.dylib"),
    reason="Requires macOS with FUSE-T installed"
)


class TestIntegration:
    """Integration tests that mount the filesystem."""
    
    @pytest.fixture
    def mount_point(self):
        """Create a temporary mount point."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mount_path = Path(tmpdir) / "mount"
            mount_path.mkdir()
            yield mount_path
    
    @pytest.fixture
    def json_file(self):
        """Create a temporary JSON file."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json_data = [
                {
                    "type": "directory",
                    "name": "/",
                    "contents": [
                        {"type": "file", "name": "test.txt", "size": 100},
                        {"type": "file", "name": "empty.txt", "size": 0},
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
            json.dump(json_data, f)
            f.flush()
            yield f.name
        os.unlink(f.name)
    
    def mount_fs(self, json_file, mount_point, extra_args=None):
        """Mount the filesystem in a subprocess."""
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jsonfs.py"),
            json_file,
            str(mount_point),
            "--log-level", "ERROR",  # Reduce noise
            "--report-stats"  # Disable stats reporting
        ]
        if extra_args:
            cmd.extend(extra_args)
        
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait for mount by checking if files appear
        for i in range(50):  # 5 seconds timeout
            time.sleep(0.1)
            try:
                files = os.listdir(mount_point)
                # Check for expected files to confirm it's mounted
                if any(f in files for f in [".metadata_never_index", "test.txt", "empty.txt"]):
                    return proc
            except OSError:
                pass
            
            # Check if process died
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                raise RuntimeError(f"Mount failed: {stderr.decode()}")
        
        # Timeout
        proc.terminate()
        stdout, stderr = proc.communicate()
        raise RuntimeError(f"Mount timeout: {stderr.decode()}")
    
    def test_basic_mount(self, json_file, mount_point):
        """Test basic mounting and unmounting."""
        proc = self.mount_fs(json_file, mount_point)
        
        try:
            # Check if mounted by listing files
            files = os.listdir(mount_point)
            assert len(files) > 0  # Should have files
            
            # List root directory
            files = os.listdir(mount_point)
            assert "test.txt" in files
            assert "empty.txt" in files
            assert "subdir" in files
            
        finally:
            # Unmount
            if sys.platform == "darwin":
                subprocess.run(["umount", str(mount_point)])
            else:
                subprocess.run(["fusermount", "-u", str(mount_point)])
            proc.terminate()
            proc.wait()
    
    def test_file_reading(self, json_file, mount_point):
        """Test reading file contents."""
        proc = self.mount_fs(json_file, mount_point)
        
        try:
            # Read file
            test_file = mount_point / "test.txt"
            content = test_file.read_bytes()
            assert len(content) == 100
            assert content == b'\x00' * 100  # Default fill char
            
            # Read empty file
            empty_file = mount_point / "empty.txt"
            content = empty_file.read_bytes()
            assert len(content) == 0
            
            # Read nested file
            nested_file = mount_point / "subdir" / "nested.txt"
            content = nested_file.read_bytes()
            assert len(content) == 50
            
        finally:
            if sys.platform == "darwin":
                subprocess.run(["umount", str(mount_point)])
            else:
                subprocess.run(["fusermount", "-u", str(mount_point)])
            proc.terminate()
            proc.wait()
    
    def test_custom_fill_char(self, json_file, mount_point):
        """Test custom fill character."""
        proc = self.mount_fs(json_file, mount_point, ["--fill-char", "X"])
        
        try:
            test_file = mount_point / "test.txt"
            content = test_file.read_bytes()
            assert len(content) == 100
            assert content == b'X' * 100
            
        finally:
            if sys.platform == "darwin":
                subprocess.run(["umount", str(mount_point)])
            else:
                subprocess.run(["fusermount", "-u", str(mount_point)])
            proc.terminate()
            proc.wait()
    
    def test_file_stats(self, json_file, mount_point):
        """Test file statistics."""
        proc = self.mount_fs(json_file, mount_point)
        
        try:
            # Check file stats
            test_file = mount_point / "test.txt"
            stat = test_file.stat()
            assert stat.st_size == 100
            assert stat.st_mode & 0o444  # Read permission
            
            # Check directory stats
            subdir = mount_point / "subdir"
            stat = subdir.stat()
            assert stat.st_mode & 0o40000  # Is directory
            
        finally:
            if sys.platform == "darwin":
                subprocess.run(["umount", str(mount_point)])
            else:
                subprocess.run(["fusermount", "-u", str(mount_point)])
            proc.terminate()
            proc.wait()
    
    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS specific test")
    def test_macos_control_files(self, json_file, mount_point):
        """Test macOS control files are created."""
        proc = self.mount_fs(json_file, mount_point)
        
        try:
            files = os.listdir(mount_point)
            assert ".metadata_never_index" in files
            
        finally:
            subprocess.run(["umount", str(mount_point)])
            proc.terminate()
            proc.wait()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])