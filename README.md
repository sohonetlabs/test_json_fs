# JSONFileSystem

Read-only FUSE filesystem based on JSON data from the tree -J -s command output.

Create a "imaginary file system" that only exists when you read from it.

This allows massive file systems to be mounted and used with out taking up any disk space.

Large structures can be emulated, for testing software.

## SEE NOTE

## usage :- 

    python jsonfs.py -h
    usage: jsonfs.py [-h] [--debug] [--fill-char FILL_CHAR] [--rate-limit RATE_LIMIT] [--iop-limit IOP_LIMIT] [--no-report] json_file mount_point

    Mount a JSON file as a read-only filesystem

    positional arguments:
    json_file             Path to the JSON file describing the filesystem
    mount_point           Mount point for the filesystem

    options:
    -h, --help            show this help message and exit
    --debug               Enable debug output
    --fill-char FILL_CHAR
                          Character to fill read data with (default: null byte)
    --rate-limit RATE_LIMIT
                          Rate limit in seconds (e.g., 0.1 for 100ms delay)
    --iop-limit IOP_LIMIT
                          IOP limit per second (e.g., 100 for 100 IOPS)
    --no-report           Disable IOPS and data transfer reporting


## example :-

    # create mount point 
    mkdir ./jsonfs
    # mount the filesystem
    python jsonfs.py ./test.json --no-report --debug

you will now have a filesystem mounted on ./jsonfs

    Root structure:
    test (directory, size: 384.00 bytes (384 bytes))
        filename000000000.txt (file, size: 1.00 KB (1024 bytes))
        filename000000001.txt (file, size: 1.00 KB (1024 bytes))
        filename000000002.txt (file, size: 1.00 KB (1024 bytes))
        filename000000003.txt (file, size: 1.00 KB (1024 bytes))
        filename000000004.txt (file, size: 1.00 KB (1024 bytes))
        ... (5 more items)
        File: filename000000000.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000001.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000002.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000003.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000004.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000005.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000006.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000007.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000008.txt, Size: 1.00 KB (1024 bytes)
        File: filename000000009.txt, Size: 1.00 KB (1024 bytes)
        Directory: test, Size: 10.00 KB (10240 bytes)
        Total size: 10.00 KB (10240 bytes)
        Total files: 10

## output from df

    df -h ./jsonfs
    Filesystem        Size    Used   Avail Capacity iused ifree %iused  Mounted on
    fuse-t:/jsonfs    12Ki    12Ki     0Bi   100%       0     0     -   /Users/foo/jsonfs/jsonfs

##  ls -ltr ./jsonfs
    total 20
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000009.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000008.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000007.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000006.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000005.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000004.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000003.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000002.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000001.txt
    -r--r--r--  2 root  wheel  1024 29 Jul 14:05 filename000000000.txt

## NOTE  This script uses fuse-t which is a kext-less implementation of FUSE for macOS that uses NFS v4 local server instead of a kernel extension.

see 
https://www.fuse-t.org/

to install on macOS

    brew install fuse-t

You will need to modify pyfuse and patch the following line

            _libfuse_path = (find_library('fuse4x') or find_library('osxfuse') or
                         find_library('fuse'))

to

            _libfuse_path = (find_library('fuse4x') or find_library('osxfuse') or
                        find_library('fuse') or find_library('libfuse-t'))


