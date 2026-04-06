#!/usr/bin/env python3
"""
Automated build script for classic Mac via AppleBridge.

Usage:
    uv run python build.py <project_dir> [--run]
    uv run python build.py MeinMac:MPW:MultiFile: --run

Features:
    - Compiles all .c files with SC compiler
    - Captures and reports compile errors via stderr redirect
    - Links object files with required libraries
    - Sets file type for executable
    - Optionally runs the built application
"""
import socket
import sys
import re
import argparse
from pathlib import Path


def send_command(command: str, host: str = '127.0.0.1', port: int = 9001) -> str:
    """Send command to AppleBridge server"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        sock.sendall(command.encode('utf-8'))
        sock.shutdown(socket.SHUT_WR)

        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk

        return response.decode('utf-8', errors='replace')
    finally:
        sock.close()


def parse_response(response: str) -> dict:
    """Parse AppleBridge response into components"""
    result = {
        'status': None,
        'stdout': '',
        'stderr': '',
        'success': False
    }

    lines = response.strip().split('\n')
    for line in lines:
        if line.startswith('STATUS:'):
            result['status'] = int(line.split(':')[1])
        elif line.startswith('STDOUT:'):
            # Next content until STDERR is stdout
            pass
        elif line.startswith('STDERR:'):
            pass
        elif 'Got:' in line:
            result['success'] = True
        elif 'NoDir:-1701' in line or 'Empty' in line:
            result['success'] = False

    # Extract stdout content (between STATUS and STDERR lines)
    if 'STDOUT:' in response:
        parts = response.split('STDERR:')
        if len(parts) > 1:
            stdout_part = parts[0]
            # Get content after STDOUT:N line
            stdout_lines = stdout_part.split('\n')
            for i, line in enumerate(stdout_lines):
                if line.startswith('STDOUT:'):
                    result['stdout'] = '\n'.join(stdout_lines[i+1:]).strip()
                    break

    return result


def file_exists(path: str) -> bool:
    """Check if file exists on Mac"""
    response = send_command(f'Exists {path}')
    return 'Got:' in response and 'NoDir' not in response


def get_file_list(directory: str, pattern: str = '') -> list:
    """Get list of files in directory"""
    cmd = f'Files {directory}'
    if pattern:
        cmd = f'Files {directory}{pattern}'
    response = send_command(cmd)
    result = parse_response(response)

    if result['stdout']:
        # Files are space or newline separated
        files = result['stdout'].replace('\r', '\n').split()
        return [f for f in files if f and not f.startswith(':')]
    return []


def compile_file(source: str, output: str, err_file: str) -> tuple[bool, str]:
    """
    Compile a single C file.
    Returns (success, error_message)
    """
    # Compile with stderr redirect
    cmd = f'SC {source} -o {output} ≥ {err_file}'
    send_command(cmd)

    # Check if .o file was created
    success = file_exists(output)

    # Get any errors/warnings
    err_response = send_command(f'Catenate {err_file}')
    err_result = parse_response(err_response)
    errors = err_result['stdout'] if err_result['stdout'] else ''

    return success, errors


def link_files(obj_files: list, output: str, err_file: str) -> tuple[bool, str]:
    """
    Link object files into executable.
    Returns (success, error_message)
    """
    objs = ' '.join(obj_files)
    libs = '"{Libraries}Interface.o" "{Libraries}MacRuntime.o"'

    cmd = f'Link -model far {objs} {libs} -o {output} ≥ {err_file}'
    send_command(cmd)

    # Check if executable was created
    success = file_exists(output)

    # Get any errors
    err_response = send_command(f'Catenate {err_file}')
    err_result = parse_response(err_response)
    errors = err_result['stdout'] if err_result['stdout'] else ''

    return success, errors


def set_file_type(path: str) -> bool:
    """Set file type to APPL"""
    cmd = f"SetFile -t APPL -c '????' {path}"
    send_command(cmd)
    return True


def run_app(path: str) -> str:
    """Launch the application"""
    response = send_command(path)
    return response


def build_project(project_dir: str, app_name: str = None, run: bool = False) -> bool:
    """
    Build a project from a directory.

    Args:
        project_dir: Mac path to project directory (e.g., MeinMac:MPW:Project:)
        app_name: Output application name (default: derived from directory)
        run: Whether to run the app after building

    Returns:
        True if build succeeded
    """
    # Ensure directory ends with :
    if not project_dir.endswith(':'):
        project_dir += ':'

    # Derive app name from directory if not provided
    if not app_name:
        # Get last component of path
        parts = project_dir.rstrip(':').split(':')
        app_name = parts[-1] if parts else 'App'

    print(f"Building project: {project_dir}")
    print(f"Output: {app_name}")
    print("-" * 40)

    # Get list of .c files
    response = send_command(f'Files {project_dir}')
    result = parse_response(response)

    if not result['stdout']:
        print(f"Error: Could not list files in {project_dir}")
        return False

    # Parse file list - look for .c files
    all_files = result['stdout'].replace('\r', ' ').split()
    c_files = [f for f in all_files if f.endswith('.c')]

    if not c_files:
        print("Error: No .c files found")
        return False

    print(f"Found {len(c_files)} source file(s): {', '.join(c_files)}")

    # Compile each file
    obj_files = []
    all_errors = []
    err_file = f'{project_dir}build.err'

    for c_file in c_files:
        source = f'{project_dir}{c_file}'
        obj = source.replace('.c', '.o')

        print(f"  Compiling {c_file}...", end=' ', flush=True)
        success, errors = compile_file(source, obj, err_file)

        if success:
            print("OK")
            obj_files.append(obj)
            # Show warnings if any (but not the header)
            if errors and '#Error' not in errors:
                warnings = [l for l in errors.split('\r') if 'Warning' in l]
                for w in warnings:
                    print(f"    Warning: {w.strip()}")
        else:
            print("FAILED")
            # Parse and show errors
            if errors:
                for line in errors.split('\r'):
                    if '#Error' in line or 'Error' in line:
                        print(f"    {line.strip()}")
            all_errors.append(c_file)

    if all_errors:
        print(f"\nBuild failed: {len(all_errors)} file(s) had errors")
        return False

    # Link
    output = f'{project_dir}{app_name}'
    print(f"  Linking {len(obj_files)} object(s)...", end=' ', flush=True)

    success, errors = link_files(obj_files, output, err_file)

    if not success:
        print("FAILED")
        if errors:
            for line in errors.split('\r'):
                if line.strip():
                    print(f"    {line.strip()}")
        return False

    print("OK")

    # Set file type
    print(f"  Setting file type...", end=' ', flush=True)
    set_file_type(output)
    print("OK")

    print("-" * 40)
    print(f"Build successful: {output}")

    # Run if requested
    if run:
        print(f"\nLaunching {app_name}...")
        run_app(output)

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Build classic Mac project via AppleBridge',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    uv run python build.py MeinMac:MPW:MultiFile:
    uv run python build.py MeinMac:MPW:MyProject: --name MyApp --run
        """
    )
    parser.add_argument('project_dir', help='Mac path to project directory')
    parser.add_argument('--name', '-n', help='Output application name')
    parser.add_argument('--run', '-r', action='store_true', help='Run app after building')

    args = parser.parse_args()

    success = build_project(args.project_dir, args.name, args.run)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
