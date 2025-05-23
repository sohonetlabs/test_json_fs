"""Integration tests for FUSE mounting on macOS."""

import json
import os
import sys
import tempfile
import time
import subprocess
import pytest
from pathlib import Path

# Add parent directory to path to import jsonfs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS specific tests")
class TestMacOSMount:
    """Test FUSE-T mounting on macOS."""
    
    @pytest.fixture
    def mount_dir(self):
        """Create a temporary mount directory."""
        # Use a path in /tmp which is more reliable for FUSE on macOS
        mount_path = Path(tempfile.mkdtemp(prefix="jsonfs_test_"))
        yield mount_path
        # Cleanup
        if mount_path.exists():
            import shutil
            shutil.rmtree(mount_path)
    
    @pytest.fixture
    def json_file(self):
        """Create a test JSON file."""
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
    
    def mount_fs(self, json_file, mount_dir, extra_args=None):
        """Mount filesystem and return the process."""
        cmd = [
            sys.executable,
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jsonfs.py"),
            json_file,
            str(mount_dir),
            "--log-level", "ERROR",
            "--report-stats"  # Disable stats reporting for cleaner tests
        ]
        if extra_args:
            cmd.extend(extra_args)
        
        # Start mount process
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait for mount (check if directory becomes accessible)
        for i in range(50):  # 5 seconds timeout
            time.sleep(0.1)
            try:
                # Try to list the directory - if it works, it's mounted
                files = os.listdir(mount_dir)
                # Check for expected files to confirm it's our filesystem
                if any(f in files for f in [".metadata_never_index", "test.txt", "empty.txt"]):
                    return proc
            except OSError:
                # Directory not ready yet
                pass
            
            # Check if process died
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                raise RuntimeError(f"Mount failed: {stderr.decode()}")
        
        # Timeout
        proc.terminate()
        stdout, stderr = proc.communicate()
        raise RuntimeError(f"Mount timeout: {stderr.decode()}")
    
    def unmount(self, mount_dir, proc):
        """Unmount the filesystem."""
        # First try graceful unmount
        result = subprocess.run(["umount", str(mount_dir)], capture_output=True)
        
        if result.returncode != 0:
            # Force unmount if needed
            subprocess.run(["umount", "-f", str(mount_dir)], capture_output=True)
        
        # Terminate the process
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    
    def test_basic_mount(self, json_file, mount_dir):
        """Test basic mounting on macOS."""
        proc = None
        try:
            proc = self.mount_fs(json_file, mount_dir)
            
            # List files
            files = os.listdir(mount_dir)
            assert "test.txt" in files
            assert "empty.txt" in files
            assert "subdir" in files
            
            # On macOS, we should see the control files
            assert ".metadata_never_index" in files
            
        finally:
            if proc:
                self.unmount(mount_dir, proc)
    
    def test_read_files(self, json_file, mount_dir):
        """Test reading files."""
        proc = None
        try:
            proc = self.mount_fs(json_file, mount_dir)
            
            # Read a file
            test_file = mount_dir / "test.txt"
            content = test_file.read_bytes()
            assert len(content) == 100
            assert content == b'\x00' * 100
            
            # Read empty file
            empty_file = mount_dir / "empty.txt"
            content = empty_file.read_bytes()
            assert len(content) == 0
            
            # Read nested file
            nested_file = mount_dir / "subdir" / "nested.txt"
            content = nested_file.read_bytes()
            assert len(content) == 50
            
        finally:
            if proc:
                self.unmount(mount_dir, proc)
    
    def test_custom_fill_char(self, json_file, mount_dir):
        """Test with custom fill character."""
        proc = None
        try:
            proc = self.mount_fs(json_file, mount_dir, ["--fill-char", "X"])
            
            test_file = mount_dir / "test.txt"
            content = test_file.read_bytes()
            assert content == b'X' * 100
            
        finally:
            if proc:
                self.unmount(mount_dir, proc)
    
    def test_file_stats(self, json_file, mount_dir):
        """Test file statistics."""
        proc = None
        try:
            proc = self.mount_fs(json_file, mount_dir)
            
            # Check file
            test_file = mount_dir / "test.txt"
            stat = test_file.stat()
            assert stat.st_size == 100
            
            # Check directory
            subdir = mount_dir / "subdir"
            assert subdir.is_dir()
            
        finally:
            if proc:
                self.unmount(mount_dir, proc)


# Simple test runner for manual testing
if __name__ == "__main__":
    print("Running macOS FUSE mount tests...")
    print("Make sure FUSE-T is installed: brew install fuse-t")
    
    # Run a simple test
    import tempfile
    import json
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json') as f:
        json.dump([{
            "type": "directory",
            "name": "/",
            "contents": [
                {"type": "file", "name": "hello.txt", "size": 13}
            ]
        }], f)
        f.flush()
        
        mount_dir = tempfile.mkdtemp(prefix="jsonfs_manual_test_")
        print(f"Mounting to: {mount_dir}")
        
        cmd = [sys.executable, "jsonfs.py", f.name, mount_dir]
        print(f"Running: {' '.join(cmd)}")
        
        try:
            proc = subprocess.Popen(cmd)
            time.sleep(2)
            
            print("\nFiles in mount:")
            for file in os.listdir(mount_dir):
                print(f"  {file}")
            
            print("\nPress Enter to unmount...")
            input()
            
        finally:
            subprocess.run(["umount", mount_dir])
            if 'proc' in locals():
                proc.terminate()
            os.rmdir(mount_dir)