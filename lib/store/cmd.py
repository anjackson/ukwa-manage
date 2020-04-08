'''
This contains the CLI tool for uploading to HDFS _very carefully_...
'''
import os
import csv
import sys
import json
import logging
import argparse
from lib.store.webhdfs import WebHDFSStore

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s: %(levelname)s - %(name)s - %(message)s')

logger = logging.getLogger(__name__)

# Defaults to using the production HDFS (via 'safe' gateway):
# TODO Switch to a variable store URI for different backends.
DEFAULT_STORE = os.environ.get("STORE_URI", "webhdfs://access@hdfs.api.wa.bl.uk/")
DEFAULT_WEBHDFS = os.environ.get("WEBHDFS_URL", "http://hdfs.api.wa.bl.uk/")
DEFAULT_WEBHDFS_USER = os.environ.get("WEBHDFS_USERNAME", "access")

# Fields to output in the CSV version:
CSV_FIELDNAMES =  ['permissions', 'number_of_replicas', 'userid', 'groupid', 'filesize', 'modified_at', 'filename']

def main():
    # Set up a parser:
    parser = argparse.ArgumentParser(prog='store')

    # Common arguments:
    parser.add_argument('-w', '--webhdfs-url', type=str, help='The WebHDFS URL to talk to (defaults to %s).' % DEFAULT_WEBHDFS, 
        default=DEFAULT_WEBHDFS)
    parser.add_argument('-u', '--webhdfs-user', type=str, help='The WebHDFS user to act as (defaults to %s).' % DEFAULT_WEBHDFS_USER, 
        default=DEFAULT_WEBHDFS_USER)
    parser.add_argument('--dry-run', action='store_true', help='Do not modify the TrackDB.')
    parser.add_argument('-i', '--indent', type=int, help='Number of spaces to indent when emitting JSON.')

    # Use sub-parsers for different operations:
    subparsers = parser.add_subparsers(dest="op")

    # 'get' subcommand - retrieves files from the store:
    parser_get = subparsers.add_parser('get', help='Get a file from the store.')
    parser_get.add_argument('--offset', type=int, help='The byte offset to start reading from (default is 0).')
    parser_get.add_argument('--length', type=int, help='The number of bytes to read. (default is to read the whole thing)')
    parser_get.add_argument('path', type=str, help='The file to get.')
    parser_get.add_argument('local_path', type=str, help='The local file to copy to (use "-" for STDOUT).')

    # 'list' subcommand - list what's in the store:
    parser_list = subparsers.add_parser('list', help='List a folder on the store.')
    parser_list.add_argument('-r', '--recursive', action='store_true', help='List files recursively (directories are not listed).')
    parser_list.add_argument('path', type=str, help='The path to list.')

    # 'put' subcommand - upload a file or folder to the store:
    parser_up = subparsers.add_parser('put', help='Put a local file into the store.')
    parser_up.add_argument('local_path', type=str, help='The local path to read.')
    parser_up.add_argument('path', type=str, help='The store path to write to.')

    # 'delete' subcommand - delete a file from the store:
    parser_rm = subparsers.add_parser('delete', help='Delete a file from the store.')
    parser_rm.add_argument('path', type=str, help='The file to delete.')

    # And PARSE it:
    args = parser.parse_args()

    # Set up client:
    st = WebHDFSStore(args.webhdfs_url, args.webhdfs_user)

    # Ops:
    logger.debug("Got args: %s" % args)
    if args.op == 'list':
        writer = csv.DictWriter(sys.stdout, fieldnames=CSV_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        for info in st.list(args.path, args.recursive):
            writer.writerow(info)
    elif args.op == 'get':
        reader = st.read(args.path, offset = args.offset, length = args.length)
        if args.local_path == '-':
            for data in reader:
                sys.stdout.buffer.write(data)
        else:
            if os.path.exists(args.local_path):
                raise Exception("Path %s already exists! Refusing to overwrite.")
            else:
                with open(args.local_path, 'wb') as f:
                    for data in reader:
                        f.write(data)

    elif args.op == 'put':
        st.put(args.local_path, args.path)
    elif args.op == 'rm':
        st.rm(args.path)
    else:
        raise Exception("Not implemented!")


if __name__ == "__main__":
    main()
