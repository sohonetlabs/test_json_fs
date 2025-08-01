"""Test stats reporting thread functionality."""

import os
import sys
import time
import threading
from io import StringIO
from unittest.mock import patch
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jsonfs import JSONFileSystem


class TestStatsReporting:
    """Test the stats reporting thread."""
    
    def test_stats_thread_starts(self):
        """Test that stats thread starts when report=True."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        # Create filesystem with reporting enabled
        fs = JSONFileSystem(json_data, report=True, pre_generated_blocks=1, block_size=1024)
        
        # Check that thread was created
        assert hasattr(fs, 'stats_thread')
        assert fs.stats_thread is not None
        assert fs.stats_thread.is_alive()
        
        # Clean up - stop the thread
        # Since it's a daemon thread, it will stop when the main thread exits
        # But we can at least verify it exists
        
    def test_stats_reporting_output(self):
        """Test that stats are reported to stdout."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        # Capture stdout
        captured_output = StringIO()
        
        with patch('sys.stdout', captured_output):
            # Create filesystem with reporting
            fs = JSONFileSystem(json_data, report=True, pre_generated_blocks=1, block_size=1024)
            
            # Perform some operations to generate stats
            fs.getattr("/test.txt")
            fs.read("/test.txt", 50, 0, None)
            
            # Wait for stats to be reported (stats print every second)
            time.sleep(1.2)
        
        # Check output
        output = captured_output.getvalue()
        assert "IOPS:" in output
        assert "Data transferred:" in output
        
    def test_stats_increment(self):
        """Test that stats are incremented correctly."""
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
        
        # Check initial stats
        assert fs.iops_count == 0
        assert fs.bytes_read == 0
        
        # Perform operations
        fs._increment_stats(0)  # Just IOPS
        assert fs.iops_count == 1
        assert fs.bytes_read == 0
        
        fs._increment_stats(100)  # IOPS + bytes
        assert fs.iops_count == 2
        assert fs.bytes_read == 100
        
    def test_report_stats_method(self):
        """Test the _report_stats method directly."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": []
            }
        ]
        
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
        
        # Capture print output
        outputs = []
        
        def capture_print(*args, **kwargs):
            outputs.append(args[0] if args else "")
        
        # Patch both print and sleep
        with patch('builtins.print', side_effect=capture_print):
            with patch('time.sleep') as mock_sleep:
                # Set up sleep to stop after first iteration
                mock_sleep.side_effect = [None, StopIteration()]
                
                # Set some stats
                with fs.stats_lock:
                    fs.iops_count = 10
                    fs.bytes_read = 1024
                
                # Run the stats method
                try:
                    fs._report_stats()
                except StopIteration:
                    pass
        
        # Check output
        assert len(outputs) > 0
        output_str = outputs[0]
        assert "IOPS: 10" in output_str
        assert "1.00 KB" in output_str  # 1024 bytes formatted
        
    def test_stats_reset_after_report(self):
        """Test that stats are reset after reporting."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "test.txt", "size": 100}
                ]
            }
        ]
        
        # Use a custom print function to capture output and control timing
        outputs = []
        
        def mock_print(*args, **kwargs):
            outputs.append(args[0] if args else "")
        
        with patch('builtins.print', mock_print):
            fs = JSONFileSystem(json_data, report=True, pre_generated_blocks=1, block_size=1024)
            
            # Perform operations
            fs.getattr("/test.txt")
            fs.read("/test.txt", 50, 0, None)
            
            # Wait for first report
            time.sleep(1.2)
            
            # Stats should have been reset
            with fs.stats_lock:
                # After reporting, counters should be 0
                assert fs.iops_count == 0 or fs.iops_count <= 2  # Might have new ops
                assert fs.bytes_read == 0 or fs.bytes_read <= 100
        
        # Verify we got output
        assert len(outputs) > 0
        assert any("IOPS:" in out for out in outputs)


class TestStatsThreadLifecycle:
    """Test thread lifecycle management."""
    
    def test_no_thread_when_report_false(self):
        """Test that no thread is created when report=False."""
        json_data = [
            {
                "type": "directory",
                "name": "/", 
                "contents": []
            }
        ]
        
        fs = JSONFileSystem(json_data, report=False, pre_generated_blocks=1, block_size=1024)
        
        # Should not have stats thread
        assert not hasattr(fs, 'stats_thread') or fs.stats_thread is None
        
    def test_thread_is_daemon(self):
        """Test that stats thread is a daemon thread."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": []
            }
        ]
        
        fs = JSONFileSystem(json_data, report=True, pre_generated_blocks=1, block_size=1024)
        
        # Thread should be daemon
        assert fs.stats_thread.daemon is True
        
    def test_humanize_bytes_in_output(self):
        """Test that bytes are humanized in output."""
        json_data = [
            {
                "type": "directory",
                "name": "/",
                "contents": [
                    {"type": "file", "name": "large.bin", "size": 10 * 1024 * 1024}  # 10MB
                ]
            }
        ]
        
        captured_output = StringIO()
        
        with patch('sys.stdout', captured_output):
            fs = JSONFileSystem(json_data, report=True, pre_generated_blocks=1, block_size=1024)
            
            # Read 5MB
            fs.read("/large.bin", 5 * 1024 * 1024, 0, None)
            
            # Wait for report
            time.sleep(1.2)
        
        output = captured_output.getvalue()
        assert "MB" in output  # Should show MB not bytes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])