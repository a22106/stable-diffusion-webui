# duplicate files in the two directories
from pathlib import Path
from shutil import copyfile
import shutil
import filecmp
import os
import argparse

parser = argparse.ArgumentParser()
parser.add_argument('--dir1', '-d1', help='directory 1', required=True, type=str)
parser.add_argument('--dir2', '-d2', help='directory 2', required=True, type=str)
parser.add_argument('--compare', '-c', help='compare content of the files', action='store_true')
args = parser.parse_args()

# make whole files in the directories to be equal. even if the files are not in the same directory and in the subdirectory
# copy files from dir1 to dir2 and vice versa
def copy_files(dir1, dir2, compare_content: bool = False):
    
    for file1 in dir1.glob('**/*'):
        if file1.is_file():
            
            file2 = dir2 / file1.relative_to(dir1)
            if file2.exists() == False:
                file2.parent.mkdir(parents=True, exist_ok=True)
                copyfile(file1, file2)
                print(f'copied {file1} to {file2}')
            elif file2.exists() and compare_content:
                # Compare the contents of the old and new files
                if not filecmp.cmp(file1, file2):
                    # If the files are different, compare their modified times
                    if os.path.getmtime(file2) > os.path.getmtime(file1):
                        # If the new file is newer, copy it over the old file
                        shutil.copy(file2, file1)
                        print(f'copied {file1} to {file2}')
            else:
                continue
                
DIR1 = Path(args.dir1)
DIR2 = Path(args.dir2)
copy_files(DIR1, DIR2, args.compare)
copy_files(DIR2, DIR1, args.compare)