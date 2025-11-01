#!/usr/bin/env python3
"""
Analyze Python codebase statistics.

Counts characters, lines, and methods in Python files while respecting
.gitignore patterns and excluding specified directories.
"""

import ast
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple
import subprocess


def get_gitignored_files(root_dir: Path) -> Set[Path]:
    """
    Get set of files that are gitignored.

    Args:
        root_dir: Root directory of the git repository

    Returns:
        Set of absolute paths that are gitignored
    """
    try:
        # Get list of all files tracked or would be tracked by git
        result = subprocess.run(
            ["git", "ls-files", "--ignored", "--exclude-standard", "--others"],
            cwd=root_dir,
            capture_output=True,
            text=True,
            check=True
        )

        ignored_files = set()
        for line in result.stdout.strip().split('\n'):
            if line:
                ignored_files.add((root_dir / line).resolve())

        return ignored_files
    except subprocess.CalledProcessError:
        print("Warning: Could not get gitignored files, continuing without filtering")
        return set()


def count_methods_in_file(file_path: Path) -> int:
    """
    Count the number of function and method definitions in a Python file.

    Args:
        file_path: Path to the Python file

    Returns:
        Number of function/method definitions
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tree = ast.parse(f.read(), filename=str(file_path))

        method_count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                method_count += 1

        return method_count
    except (SyntaxError, UnicodeDecodeError) as e:
        print(f"Warning: Could not parse {file_path}: {e}")
        return 0


def should_exclude_path(path: Path, root_dir: Path, exclude_dirs: List[str]) -> bool:
    """
    Check if a path should be excluded based on directory exclusions.

    Args:
        path: Path to check
        root_dir: Root directory
        exclude_dirs: List of directory names to exclude

    Returns:
        True if path should be excluded
    """
    try:
        relative_path = path.relative_to(root_dir)
        parts = relative_path.parts

        # Check if any excluded directory is in the path
        for exclude_dir in exclude_dirs:
            if exclude_dir in parts:
                return True

        return False
    except ValueError:
        # Path is not relative to root_dir
        return True


def analyze_python_files(
    root_dir: Path,
    exclude_dirs: List[str] = None
) -> Dict[str, any]:
    """
    Analyze all Python files in directory tree.

    Args:
        root_dir: Root directory to start analysis
        exclude_dirs: List of directory names to exclude

    Returns:
        Dictionary containing analysis results
    """
    if exclude_dirs is None:
        exclude_dirs = ['junk_drawer', 'tests']

    gitignored = get_gitignored_files(root_dir)

    total_files = 0
    total_lines = 0
    total_chars = 0
    total_methods = 0

    file_stats: List[Tuple[str, int, int, int]] = []

    for py_file in root_dir.rglob('*.py'):
        # Skip if in excluded directory
        if should_exclude_path(py_file, root_dir, exclude_dirs):
            continue

        # Skip if gitignored
        if py_file.resolve() in gitignored:
            continue

        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.count('\n') + 1
                chars = len(content)

            methods = count_methods_in_file(py_file)

            relative_path = py_file.relative_to(root_dir)
            file_stats.append((str(relative_path), lines, chars, methods))

            total_files += 1
            total_lines += lines
            total_chars += chars
            total_methods += methods

        except Exception as e:
            print(f"Warning: Could not read {py_file}: {e}")

    return {
        'total_files': total_files,
        'total_lines': total_lines,
        'total_chars': total_chars,
        'total_methods': total_methods,
        'file_stats': sorted(file_stats, key=lambda x: x[1], reverse=True)
    }


def print_results(results: Dict[str, any]) -> None:
    """
    Print analysis results in a formatted manner.

    Args:
        results: Dictionary containing analysis results
    """
    print("\n" + "=" * 80)
    print("PYTHON CODEBASE ANALYSIS")
    print("=" * 80)
    print(f"\nTotal Files:    {results['total_files']:,}")
    print(f"Total Lines:    {results['total_lines']:,}")
    print(f"Total Chars:    {results['total_chars']:,}")
    print(f"Total Methods:  {results['total_methods']:,}")
    print(f"\nAverage Lines per File:   {results['total_lines'] / results['total_files']:.1f}" if results['total_files'] > 0 else "")
    print(f"Average Methods per File: {results['total_methods'] / results['total_files']:.1f}" if results['total_files'] > 0 else "")

    print("\n" + "-" * 80)
    print("TOP 20 FILES BY LINE COUNT")
    print("-" * 80)
    print(f"{'File':<60} {'Lines':>8} {'Chars':>10} {'Methods':>8}")
    print("-" * 80)

    for filename, lines, chars, methods in results['file_stats'][:20]:
        # Truncate filename if too long
        display_name = filename if len(filename) <= 59 else "..." + filename[-56:]
        print(f"{display_name:<60} {lines:>8,} {chars:>10,} {methods:>8}")

    print("=" * 80 + "\n")


def main():
    """Main entry point for the script."""
    root_dir = Path(__file__).parent.resolve()

    print(f"Analyzing Python files in: {root_dir}")
    print("Excluding directories: junk_drawer, tests")
    print("Excluding gitignored files...")

    results = analyze_python_files(
        root_dir,
        exclude_dirs=['junk_drawer', 'tests']
    )

    print_results(results)


if __name__ == "__main__":
    main()
