# JSONFileSystem

Read-only FUSE filesystem based on JSON data from the ```tree -J -s``` command output.

Create a "imaginary file system" that only exists when you read from it.

This allows massive file systems to be mounted and used with out taking up any disk space.

Large structures can be emulated, for testing software.

## SEE NOTE for Macos

## Recent Changes

### v1.6.6
- Added comprehensive test suite with 99% code coverage
- Added 88 tests covering all major functionality
- Added tests for edge cases, error handling, and FUSE operations
- Added integration tests for macOS FUSE-T
- Added background thread and statistics reporting tests
- Improved code quality and reliability through extensive testing

### v1.6.5
- Added LRU cache to path sanitization for improved performance
- Cached path operations reduce CPU usage for repeated file access

### v1.6.4
- Added validation for --fill-char option to ensure single character input
- Improved help text clarity for fill-char option

### v1.6.3
- Fixed critical bug: Added missing ENODATA import for extended attributes support
- Fixed initialization order issue where logger was used before being set
- Optimized memory usage by replacing pre-generated fill buffers with LRU cache
- Fixed race condition in IOPS statistics reporting
- Improved thread safety in rate limiting implementation

## Features

- Mount a JSON file as a read-only filesystem
- Configurable logging levels
- Rate limiting for operations
- IOP (I/O operations) limiting
- IOPS and data transfer reporting
- Custom fill character for read operations or use semi random data
- Has options to make the filesystem to be deterministic, so that between 2 runs the same data will be returned, diff'ing tars of the same fs between runs, returns no differences
- Memory-efficient fill buffer caching with LRU eviction (caches up to 1000 different buffer sizes)
- Unicode normalisation options :-( 
    * NFC (Normalization Form Canonical Composition):
        * This is the most commonly used form.
        * It first decomposes characters, then recomposes them.
        * It results in the shortest equivalent string.

    * NFD (Normalization Form Canonical Decomposition):
        * This form fully decomposes characters into their base forms and combining characters.
        * It's useful for making text accent-insensitive, which can be beneficial for searching and sorting
    
    * NFKD (Normalization Form Compatibility Decomposition)
        * Characters are decomposed by compatibility, and multiple combining characters are arranged in a specific order.

    * NFKC (Normalization Form Compatibility Composition)
        * Characters are decomposed by compatibility, then recomposed by canonical equivalence.
    
    * Rabbit holes in this direction
        * [https://eclecticlight.co/2021/05/08/explainer-unicode-normalization-and-apfs/](https://eclecticlight.co/2021/05/08/explainer-unicode-normalization-and-apfs/)
        * [https://webtide.com/a-story-about-unix-unicode-java-filesystems-internationalization-and-normalization/](https://webtide.com/a-story-about-unix-unicode-java-filesystems-internationalization-and-normalization/)
        * [https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file](https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file)


### Victims/CVEs ###
Victims claimed by Big list of naughty strings fs

- [CVE-2024-44201](https://app.opencve.io/cve/CVE-2024-44201)  Processing a malicious crafted file may lead to a denial-of-service. :-) in libarchive reported to Apple, and fixed 

### semi random algorithm


- Deterministic: The same file and block number will always result in the same data being returned, providing consistency across reads and program runs.
- Pseudo-random: While the data appears random, it's generated deterministically, allowing for reproducibility.
- Memory-efficient: Instead of storing unique data for every possible file and offset, it reuses a limited number of pre-generated blocks.
- Fast access: Once generated, block retrieval is very quick, involving only a hash calculation and array lookup.
- Customizable: The number and size of pre-generated blocks can be adjusted to balance between memory usage and data variety
 


- The block cache is generated at startup in the _generate_block_cache method
- Creates a predetermined number of blocks (self.pre_generated_blocks).
- Each block is of size self.block_size.
- For each block, it generates random data using a linear congruential generator (LCG) algorithm.
- The generated blocks are stored in a list (cache).

- When reading data, the _generate_block_data method is used to select a block:
- Takes a file path and block number as input.
- Generates a hash using MD5 from the path and block number.
- Uses this hash to deterministically select a block from the pre-generated cache.
   
## Requirements

- Python 3.6+
- FUSE using fuse-t see ** NOTE **
- `fusepy` Python package

## Testing

The project includes a comprehensive test suite with **99% code coverage** across 88 tests:

### Test Categories

- **Unit tests** (`test_unit.py`): Core functionality, helpers, caching
- **Edge case tests** (`test_edge_cases.py`): Large files, special characters, rate limiting, FUSE error cases
- **CLI tests** (`test_cli.py`): Command-line argument parsing and validation
- **Integration tests** (`test_integration.py`): Full filesystem mounting (requires FUSE)
- **Main error tests** (`test_main_errors.py`): JSON validation and error handling
- **Logging tests** (`test_logging.py`): File and stdout logging functionality
- **macOS mount tests** (`test_macos_mount.py`): macOS-specific FUSE-T integration
- **Stats thread tests** (`test_stats_thread.py`): Background statistics reporting

### Running Tests

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install test dependencies
pip install -r requirements.txt
pip install pytest pytest-cov

# Run all tests
pytest

# Run all tests with coverage report
pytest --cov=jsonfs --cov-report=term-missing

# Run specific test categories
pytest tests/test_unit.py -v              # Core functionality
pytest tests/test_edge_cases.py -v        # Edge cases and error conditions
pytest tests/test_cli.py -v               # CLI tests
pytest tests/test_integration.py -v       # Integration tests (macOS only)

# Generate HTML coverage report
pytest --cov=jsonfs --cov-report=html
# Open htmlcov/index.html in browser
```

### Test Coverage

Current test coverage: **99%** (413/417 lines covered)

The test suite covers:
- All core filesystem operations (read, getattr, readdir, etc.)
- Error handling and edge cases
- Rate limiting and IOP limiting
- Unicode normalization
- Large file support (>4GB)
- Semi-random data generation
- LRU caching for performance
- Background statistics reporting
- Command-line argument validation
- JSON structure validation

## Limitations

The filesystem is read-only. Write operations will raise a "Read-only file system" error.
The content of files is filled with a placeholder character (default is null byte), OR can be semi random data.
** now comparable in speed **
Symlinks are not supported.

## usage :- 

    usage: jsonfs.py [-h] [--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}] [--rate-limit RATE_LIMIT] [--iop-limit IOP_LIMIT] [--report-stats] [--log-to-syslog] [--version] [--block-size BLOCK_SIZE] [--pre-generated-blocks PRE_GENERATED_BLOCKS]
                 [--seed SEED] [--no-macos-cache-files] [--ignore-appledouble] [--uid UID] [--gid GID] [--mtime MTIME] [--unicode-normalization {NFC,NFD,NFKC,NFKD,none}] [--fill-char FILL_CHAR | --semi-random]
                 json_file mount_point

    Mount a JSON file as a read-only filesystem

    positional arguments:
    json_file             Path to the JSON file describing the filesystem
    mount_point           Mount point for the filesystem

    options:
    -h, --help            show this help message and exit
    --log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
                            Set the logging level (default: INFO)
    --rate-limit RATE_LIMIT
                            Rate limit in seconds (e.g., 0.1 for 100ms delay)
    --iop-limit IOP_LIMIT
                            IOP limit per second (e.g., 100 for 100 IOPS)
    --report-stats        Enable IOPS and data transfer reporting
    --log-to-syslog       Log to syslog instead of stdout
    --version             Show the version number and exit
    --block-size BLOCK_SIZE
                            Size of blocks for semi-random data generation (e.g., 1M, 2G, 512K). Default: 128K
    --pre-generated-blocks PRE_GENERATED_BLOCKS
                            Number of pre-generated blocks to use for semi-random data generation. Default: 100
    --seed SEED           Seed for random number generation. If not provided, the random number 4 is used.
    --no-macos-cache-files
                            Do not add macOS control files to prevent caching
    --ignore-appledouble  Suppress warnings about missing AppleDouble (._) files
    --uid UID             Set the UID for all files and directories (default: current user's UID)
    --gid GID             Set the GID for all files and directories (default: current user's GID)
    --mtime MTIME         Set the modification time for all files and directories (default: 2017-10-17)
    --unicode-normalization {NFC,NFD,NFKC,NFKD,none}
                            Unicode normalization form to use (default: NFD, also supports NFC, NFKC, NFKD, or 'none' for no normalization) see https://www.unicode.org/faq/normalization.html for more information
    --fill-char FILL_CHAR
                            Single character to fill read data with (default: null byte)
    --semi-random         Use semi-random data for file contents

## Example fs layouts in the examples directory

### test.json

Basic example

### 32bit\_tests.json

6 files to exercise signed and unsigned 32 bit file sizes

### imdbfslayout.json.zip               

large set of files from ai training set
Total size: 265.65 GB (285244024192 bytes)
Total files: 460723 

### tartest\_test\_one\_dir.json

For testing tar has one file per directory of sizes between 0 and 1024

### tartest\_test\_dir\_spacing.json     

For testing tar all the files in one directory of sizes between 0 and 1024

### big\_list\_of\_naughty\_strings\_fs.json 

Fuzzing systems with file names, based on https://github.com/minimaxir/big-list-of-naughty-strings/blob/master/blns.txt


## example :-

    # create mount point 
    mkdir ./jsonfs
    # mount the filesystem
    

    python jsonfs.py ./example/test.json ./jsonfs

you will now have a filesystem mounted on ./jsonfs

    python jsonfs.py ./example/test.json ./jsonfs

        2024-07-30 15:10:59,537 - INFO - Starting JSONFileSystem version 1.6.5 with log level: INFO
        2024-07-30 15:10:59,538 - INFO - Using seed: 4
        2024-07-30 15:10:59,538 - INFO - Generating 100 blocks of size 128.00 KB
        2024-07-30 15:11:00,890 - INFO - Block cache generation took 1.35 seconds
        2024-07-30 15:11:00,890 - INFO - Initializing JSONFileSystem
        2024-07-30 15:11:00,890 - INFO - Fill mode: fill_char
        2024-07-30 15:11:00,890 - INFO - Block size: 128.00 KB
        2024-07-30 15:11:00,890 - INFO - Pre-generated blocks: 100
        2024-07-30 15:11:00,890 - INFO - Rate limit: 0 seconds
        2024-07-30 15:11:00,890 - INFO - IOP limit: 0 IOPS
        2024-07-30 15:11:00,890 - INFO - Total size: 10.00 KB (10240 bytes)
        2024-07-30 15:11:00,890 - INFO - Total files: 10
        2024-07-30 15:11:00,890 - INFO - Added macOS control files to root directory
        2024-07-30 15:11:00,960 - WARNING - Path not found: /.hidden
        2024-07-30 15:11:00,961 - WARNING - Path not found: /.DS_Store
        2024-07-30 15:11:00,964 - WARNING - Path not found: /DCIM
        2024-07-30 15:11:00,966 - WARNING - Path not found: /.Spotlight-V100


## output from df

    df -h ./jsonfs
    Filesystem        Size    Used   Avail Capacity iused ifree %iused  Mounted on
    fuse-t:/jsonfs    12Ki    12Ki     0Bi   100%       0     0     -   /Users/foo/jsonfs/jsonfs

##  ls -ltr ./jsonfs
    total 20
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000009.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000008.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000007.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000006.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000005.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000004.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000003.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000002.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000001.txt
    -r--r--r--  2 ben  staff   1.0K 17 Oct  2017 filename000000000.txt

## NOTE  This script uses fuse-t which is a kext-less implementation of FUSE for macOS that uses NFS v4 local server instead of a kernel extension.

see 
[https://www.fuse-t.org/](https://www.fuse-t.org/)

to install on macOS
	
	brew tap macos-fuse-t/homebrew-cask
	brew install fuse-t
	brew install fuse-t-sshfs
    
**Now automatically detects fuse-t on macOS**

The script will automatically search for and use fuse-t on macOS. If you encounter issues, ensure fuse-t is properly installed via Homebrew as shown above.


