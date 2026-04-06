#!/bin/bash
# Copy AppleBridge Mac files to Basilisk II shared folder

SHARE_DIR="/Users/pitforster/Desktop/Share"
TARGET_DIR="$SHARE_DIR/AppleBridge"

echo "Copying AppleBridge Mac files to Basilisk II shared folder..."
echo "Target: $TARGET_DIR"

# Create target directory if it doesn't exist
mkdir -p "$TARGET_DIR"
mkdir -p "$TARGET_DIR/src"
mkdir -p "$TARGET_DIR/include"

# Copy files
echo "Copying source files..."
cp -v mac/src/*.c "$TARGET_DIR/src/"

echo "Copying header files..."
cp -v mac/include/*.h "$TARGET_DIR/include/"

echo "Copying Makefiles..."
cp -v mac/Makefile "$TARGET_DIR/"
cp -v mac/Makefile.68k "$TARGET_DIR/"

echo "Copying build notes..."
cp -v mac/BUILD_NOTES.md "$TARGET_DIR/"

echo ""
echo "✓ Files copied successfully!"
echo ""
echo "Next steps in Basilisk II:"
echo "1. Open the shared folder on Mac desktop"
echo "2. Copy AppleBridge folder to HD:MPW:AppleBridge:"
echo "3. Open MPW Shell"
echo "4. Directory HD:MPW:AppleBridge:"
echo "5. make -f Makefile.68k dirs"
echo "6. make -f Makefile.68k"
