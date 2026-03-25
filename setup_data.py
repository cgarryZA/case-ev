"""Download raw price data from Google Drive for re-running precompute.

The dashboard works out of the box using committed precomputed JSONs.
Only run this if you need to regenerate precomputed data from raw CSVs.

Usage:
    pip install gdown
    python setup_data.py
"""

import os
import sys

# Google Drive folder containing CSGO/Data (processed/ and raw/)
# Shared as restricted — only invited accounts can access
GDRIVE_FOLDER_ID = "1xd6LKGqj2nGoernD96QQXP7YRJD1O2gq"
OUT_DIR = "data/prices"


def main():
    if os.path.exists(OUT_DIR) and os.listdir(OUT_DIR):
        print(f"{OUT_DIR}/ already exists and is not empty. Skipping download.")
        print("Delete it first if you want to re-download.")
        return

    try:
        import gdown
    except ImportError:
        print("gdown is required: pip install gdown")
        sys.exit(1)

    os.makedirs(OUT_DIR, exist_ok=True)
    print("Downloading price data from Google Drive...")
    print("Note: You need access to the shared folder.")
    gdown.download_folder(
        id=GDRIVE_FOLDER_ID,
        output=OUT_DIR,
        quiet=False,
    )
    print(f"Done. Data saved to {OUT_DIR}/")
    print("You can now run: python src/precompute.py")


if __name__ == "__main__":
    main()
