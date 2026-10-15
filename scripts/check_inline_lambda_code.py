# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Compile the inline (ZipFile) Lambda code in CloudFormation templates.

Exits non-zero if any inline function has a Python syntax error.
Usage: python3 scripts/check_inline_lambda_code.py cloudformation/*.yaml
"""

import re
import sys

ZIPFILE = re.compile(r"^(?P<indent>\s*)ZipFile:\s*\|\s*$")


def inline_blocks(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    i = 0
    while i < len(lines):
        match = ZIPFILE.match(lines[i])
        i += 1
        if not match:
            continue
        start = i
        parent = len(match.group("indent"))
        body = []
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > parent):
            body.append(lines[i])
            i += 1
        indent = min(len(l) - len(l.lstrip()) for l in body if l.strip())
        yield start + 1, "\n".join(l[indent:] for l in body) + "\n"


def main(paths):
    failures = 0
    count = 0
    for path in paths:
        for line, source in inline_blocks(path):
            count += 1
            try:
                compile(source, f"{path}:{line}", "exec")
            except SyntaxError as err:
                failures += 1
                print(f"{path}:{line + (err.lineno or 1) - 1}: {err.msg}")
    print(f"Checked {count} inline Lambda functions, {failures} with syntax errors.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
