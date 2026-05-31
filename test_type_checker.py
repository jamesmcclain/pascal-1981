#!/usr/bin/env python3
"""
Comprehensive test suite for the type checker (Phase 3).

Tests cover:
  • Basic type checking (variables, assignments, scoping)
  • Advanced type checking (array indexing, field access, return types)
  • Error detection and reporting
  • Integration with the compilation pipeline
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, 'compile_to_llvm.py')


def run_type_checker(source: str) -> tuple:
    """
    Compile a Pascal program through the full pipeline.
    
    Returns: (success: bool, stderr: str)
    """
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pas', delete=False) as f:
        f.write(source)
        pas_file = f.name

    try:
        result = subprocess.run([sys.executable, DRIVER, pas_file, '/dev/null'], capture_output=True, text=True)
        return result.returncode == 0, result.stderr
    finally:
        os.unlink(pas_file)


def test_case(name: str, source: str, should_pass: bool, error_pattern: str = None) -> bool:
    """
    Run a single test case.
    
    Args:
        name: Test description
        source: Pascal source code
        should_pass: Whether compilation should succeed
        error_pattern: If should_pass=False, error message must contain this
    
    Returns: True if test passed
    """
    success, stderr = run_type_checker(source)

    if should_pass:
        if success:
            print(f"  ✓ {name}")
            return True
        else:
            print(f"  ✗ {name}: Expected success but got error")
            print(f"      {stderr.strip()}")
            return False
    else:
        if not success:
            if error_pattern and error_pattern not in stderr:
                print(f"  ✗ {name}: Error doesn't contain '{error_pattern}'")
                print(f"      {stderr.strip()}")
                return False
            print(f"  ✓ {name}")
            return True
        else:
            print(f"  ✗ {name}: Expected error but compilation succeeded")
            return False


def main():
    print("=" * 70)
    print("TYPE CHECKER TEST SUITE (Phase 3)")
    print("=" * 70)
    print()

    passed = 0
    total = 0

    # SECTION: Variable Scope (3b)
    print("Variable Scope Checking:")
    tests = [
        ("Undefined variable", "PROGRAM P; BEGIN WRITELN(x) END.", False, "Undefined variable"),
        ("Defined variable", "PROGRAM P; VAR x: INTEGER; BEGIN WRITELN(x) END.", True, None),
        ("Scope isolation - procedure", "PROGRAM P; PROCEDURE P1; VAR x: INTEGER; BEGIN END; BEGIN WRITELN(x) END.", False, "Undefined variable"),
        ("Const declaration (type checking only)", "PROGRAM P; CONST x = 42; BEGIN END.", True, None),
        ("Shadowing in nested scope", "PROGRAM P; VAR x: INTEGER; PROCEDURE P1; VAR x: INTEGER; BEGIN x := 1 END; BEGIN x := 2 END.", True, None),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Type Compatibility (3b)
    print("Type Compatibility Checking:")
    tests = [
        ("INTEGER to INTEGER assignment", "PROGRAM P; VAR x: INTEGER; BEGIN x := 42 END.", True, None),
        ("REAL to INTEGER assignment (error)", "PROGRAM P; VAR x: INTEGER; BEGIN x := 3.14 END.", False, "Cannot assign REAL"),
        ("INTEGER to BOOLEAN assignment (error)", "PROGRAM P; VAR b: BOOLEAN; BEGIN b := 1 END.", False, "Cannot assign INTEGER"),
        ("BOOLEAN type checking in expression", "PROGRAM P; VAR b: BOOLEAN; VAR x: INTEGER; BEGIN IF b THEN x := 1 END.", True, None),
        ("Multiple assignments", "PROGRAM P; VAR x: INTEGER; BEGIN x := 1; x := 2; x := 3 END.", True, None),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Control Flow Types (3b)
    print("Control Flow Type Validation:")
    tests = [
        ("IF with BOOLEAN condition", "PROGRAM P; BEGIN IF TRUE THEN WRITELN(1) END.", True, None),
        ("IF with INTEGER condition (error)", "PROGRAM P; BEGIN IF 42 THEN WRITELN(1) END.", False, "must be BOOLEAN"),
        ("WHILE with BOOLEAN condition", "PROGRAM P; VAR x: INTEGER; BEGIN x := 0; WHILE x < 10 DO x := x + 1 END.", True, None),
        ("WHILE with INTEGER condition (error)", "PROGRAM P; VAR x: INTEGER; BEGIN WHILE x DO x := x + 1 END.", False, "must be BOOLEAN"),
        ("FOR loop with INTEGER variable", "PROGRAM P; VAR i: INTEGER; BEGIN FOR i := 1 TO 10 DO WRITELN(i) END.", True, None),
        ("FOR loop with REAL variable (error)", "PROGRAM P; VAR r: REAL; BEGIN FOR r := 1.0 TO 10.0 DO WRITELN(r) END.", False, "must be INTEGER"),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Function/Procedure Calls (3b)
    print("Function/Procedure Call Validation:")
    tests = [
        ("Undefined procedure call (error)", "PROGRAM P; BEGIN UNDEFINED_PROC() END.", False, "Undefined"),
        ("Valid procedure call", "PROGRAM P; PROCEDURE P1; BEGIN END; BEGIN P1 END.", True, None),
        ("Procedure with parameters", "PROGRAM P; PROCEDURE P1(x: INTEGER); BEGIN END; BEGIN P1(42) END.", True, None),
        ("Undefined function (error)", "PROGRAM P; BEGIN WRITELN(UNDEFINED_FUNC()) END.", False, "Undefined"),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Function Return Types (3c)
    print("Function Return Type Validation:")
    tests = [
        ("Mismatched return type (error)", "PROGRAM P; FUNCTION F: INTEGER; BEGIN F := 3.14 END; BEGIN END.", False, "Cannot assign REAL"),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Array Type Checking (3c)
    print("Array Type Checking:")
    tests = [
        ("Array with REAL index (error)", "PROGRAM P; VAR a: ARRAY[1..10] OF INTEGER; BEGIN a[1.5] := 42 END.", False, "Array index must be INTEGER"),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Arithmetic & Logic (3a)
    print("Arithmetic and Logic Type Checking:")
    tests = [
        ("Integer arithmetic type (valid via type inference)", "PROGRAM P; VAR x: INTEGER; BEGIN x := 1 + 2 END.", True, None),
        ("Mixed type operations (error)", "PROGRAM P; VAR x: INTEGER; BEGIN x := 1 + TRUE END.", False, None),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SECTION: Integration & Edge Cases
    print("Integration & Edge Cases:")
    tests = [
        ("Simple program", "PROGRAM P; VAR x: INTEGER; BEGIN x := 1 END.", True, None),
        ("Const with undefined use (error)", "PROGRAM P; CONST z = 10; BEGIN WRITELN(y) END.", False, "Undefined"),
        ("Procedure with parameter type checking", "PROGRAM P; PROCEDURE P1(x: INTEGER); BEGIN END; BEGIN P1(1.5) END.", False, "type mismatch"),
    ]
    for name, src, should_pass, err in tests:
        total += 1
        if test_case(name, src, should_pass, err):
            passed += 1
    print()

    # SUMMARY
    print("=" * 70)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("=" * 70)

    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
