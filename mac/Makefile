# AppleBridge Mac Daemon Makefile
# For use with MPW (Macintosh Programmer's Workshop)

# Compiler and tools
CC = MrC
LINK = PPCLink

# Directories
SRC_DIR = :src:
INC_DIR = :include:
OBJ_DIR = :obj:
BIN_DIR = :bin:

# Compiler flags
CFLAGS = -i {INC_DIR} -w on -opt speed

# Linker flags
LDFLAGS = -o {BIN_DIR}AppleBridge

# Libraries
LIBS = ∂
    "{SharedLibraries}InterfaceLib" ∂
    "{SharedLibraries}StdCLib" ∂
    "{SharedLibraries}MathLib" ∂
    "{SharedLibraries}OpenTransportLib" ∂
    "{SharedLibraries}OpenTptInternetLib"

# Source files
SRCS = ∂
    {SRC_DIR}main.c ∂
    {SRC_DIR}transport.c ∂
    {SRC_DIR}transport_ot.c ∂
    {SRC_DIR}transport_mactcp.c ∂
    {SRC_DIR}protocol.c ∂
    {SRC_DIR}command.c ∂
    {SRC_DIR}screenshot.c ∂
    {SRC_DIR}utils.c

# Object files
OBJS = ∂
    {OBJ_DIR}main.c.o ∂
    {OBJ_DIR}transport.c.o ∂
    {OBJ_DIR}transport_ot.c.o ∂
    {OBJ_DIR}transport_mactcp.c.o ∂
    {OBJ_DIR}protocol.c.o ∂
    {OBJ_DIR}command.c.o ∂
    {OBJ_DIR}screenshot.c.o ∂
    {OBJ_DIR}utils.c.o

# Default target
all ƒ {BIN_DIR}AppleBridge

# Link
{BIN_DIR}AppleBridge ƒ {OBJS}
    {LINK} {LDFLAGS} {OBJS} {LIBS}

# Compile rules
{OBJ_DIR}main.c.o ƒ {SRC_DIR}main.c {INC_DIR}applebridge.h
    {CC} {CFLAGS} {SRC_DIR}main.c -o {OBJ_DIR}main.c.o

{OBJ_DIR}transport.c.o ƒ {SRC_DIR}transport.c {INC_DIR}transport.h {INC_DIR}transport_priv.h
    {CC} {CFLAGS} {SRC_DIR}transport.c -o {OBJ_DIR}transport.c.o

{OBJ_DIR}transport_ot.c.o ƒ {SRC_DIR}transport_ot.c {INC_DIR}applebridge.h {INC_DIR}transport_priv.h
    {CC} {CFLAGS} {SRC_DIR}transport_ot.c -o {OBJ_DIR}transport_ot.c.o

{OBJ_DIR}transport_mactcp.c.o ƒ {SRC_DIR}transport_mactcp.c {INC_DIR}applebridge.h {INC_DIR}transport_priv.h
    {CC} {CFLAGS} {SRC_DIR}transport_mactcp.c -o {OBJ_DIR}transport_mactcp.c.o

{OBJ_DIR}protocol.c.o ƒ {SRC_DIR}protocol.c {INC_DIR}applebridge.h
    {CC} {CFLAGS} {SRC_DIR}protocol.c -o {OBJ_DIR}protocol.c.o

{OBJ_DIR}command.c.o ƒ {SRC_DIR}command.c {INC_DIR}applebridge.h
    {CC} {CFLAGS} {SRC_DIR}command.c -o {OBJ_DIR}command.c.o

{OBJ_DIR}screenshot.c.o ƒ {SRC_DIR}screenshot.c {INC_DIR}applebridge.h
    {CC} {CFLAGS} {SRC_DIR}screenshot.c -o {OBJ_DIR}screenshot.c.o

{OBJ_DIR}utils.c.o ƒ {SRC_DIR}utils.c {INC_DIR}applebridge.h
    {CC} {CFLAGS} {SRC_DIR}utils.c -o {OBJ_DIR}utils.c.o

# Clean target
clean ƒ
    Delete -i {OBJ_DIR}*.o
    Delete -i {BIN_DIR}AppleBridge

# Create directories
dirs ƒ
    NewFolder {OBJ_DIR}
    NewFolder {BIN_DIR}
