#!/usr/bin/env python3
"""
Semantic validation test suite for Pascal-1981 compiler.

Tests type checking, module validation, and implementation-interface matching.
Run: python3 test_semantic.py
"""

import sys
import tempfile
import os
import shutil
from pathlib import Path

from parser import parse_file
from type_checker import PascalTypeChecker


class SemanticTest:
    """A single semantic test case."""
    
    def __init__(self, name: str, should_pass: bool, iface_code: str = None, impl_code: str = None, prog_code: str = None):
        self.name = name
        self.should_pass = should_pass
        self.iface_code = iface_code
        self.impl_code = impl_code
        self.prog_code = prog_code
        self.module_name = 'TEST'  # Default module name
        self.result = None
        
    def run(self) -> bool:
        """Run the test and return True if it passed, False if it failed."""
        tmpdir = tempfile.mkdtemp()
        
        try:
            # Write files
            if self.iface_code:
                iface_path = os.path.join(tmpdir, f'{self.module_name.lower()}.int')
                with open(iface_path, 'w') as f:
                    f.write(self.iface_code)
            
            # Determine what to type-check
            if self.impl_code:
                file_to_check = os.path.join(tmpdir, f'{self.module_name.lower()}.pas')
                with open(file_to_check, 'w') as f:
                    f.write(self.impl_code)
            elif self.prog_code:
                file_to_check = os.path.join(tmpdir, 'prog.pas')
                with open(file_to_check, 'w') as f:
                    f.write(self.prog_code)
            else:
                return False
            
            # Parse and type-check
            ast = parse_file(file_to_check)
            checker = PascalTypeChecker(source_file=file_to_check)
            result = checker.check(ast)
            self.result = result
            
            # Evaluate result
            if self.should_pass:
                return result.success
            else:
                return not result.success
                
        except Exception as e:
            # Parsing errors count as failures
            if self.should_pass:
                return False
            else:
                return True
        finally:
            shutil.rmtree(tmpdir)


def run_tests():
    """Run all semantic tests."""
    tests = [
        # ===== Implementation Validation Tests =====
        SemanticTest(
            "Implementation matches interface (procedure)",
            should_pass=True,
            iface_code="""INTERFACE;
   UNIT TEST (Proc1);
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Implementation matches interface (function)",
            should_pass=True,
            iface_code="""INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
  Func1 := X + 1
END;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Implementation with private procedures",
            should_pass=True,
            iface_code="""INTERFACE;
   UNIT TEST (DoWork);
PROCEDURE DoWork;
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
PROCEDURE DoWork;
BEGIN
END;
PROCEDURE Helper;
BEGIN
END;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Missing implementation",
            should_pass=False,
            iface_code="""INTERFACE;
   UNIT TEST (Proc1, Proc2);
PROCEDURE Proc1;
PROCEDURE Proc2;
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
PROCEDURE Proc1;
BEGIN
END;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Parameter count mismatch",
            should_pass=False,
            iface_code="""INTERFACE;
   UNIT TEST (Proc1);
PROCEDURE Proc1(X: INTEGER; Y: INTEGER);
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Parameter type mismatch",
            should_pass=False,
            iface_code="""INTERFACE;
   UNIT TEST (Proc1);
PROCEDURE Proc1(X: INTEGER);
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
PROCEDURE Proc1(X: REAL);
BEGIN
END;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Function return type mismatch",
            should_pass=False,
            iface_code="""INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1: INTEGER;
BEGIN
END;
END;
""",
            impl_code="""IMPLEMENTATION OF TEST;
FUNCTION Func1: REAL;
BEGIN
  Func1 := 0.0
END;
BEGIN
END.
""",
        ),
        
        # ===== Module Import Tests =====
        SemanticTest(
            "Program imports module (selective)",
            should_pass=True,
            iface_code="""INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
END;
END;
""",
            prog_code="""PROGRAM Prog (OUTPUT);
USES TEST (Func1);
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Program imports module (all)",
            should_pass=True,
            iface_code="""INTERFACE;
   UNIT TEST (Func1);
FUNCTION Func1(X: INTEGER): INTEGER;
BEGIN
END;
END;
""",
            prog_code="""PROGRAM Prog (OUTPUT);
USES TEST;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Missing module",
            should_pass=False,
            prog_code="""PROGRAM Prog (OUTPUT);
USES NONEXISTENT;
BEGIN
END.
""",
        ),
        
        SemanticTest(
            "Non-exported symbol import",
            should_pass=False,
            iface_code="""INTERFACE;
   UNIT TEST (PublicFunc);
FUNCTION PublicFunc: INTEGER;
FUNCTION PrivateFunc: INTEGER;
BEGIN
END;
END;
""",
            prog_code="""PROGRAM Prog (OUTPUT);
USES TEST (PrivateFunc);
BEGIN
END.
""",
        ),
    ]
    
    print("=" * 70)
    print("SEMANTIC VALIDATION TEST SUITE")
    print("=" * 70)
    print()
    
    passed = 0
    failed = 0
    
    for test in tests:
        result = test.run()
        status = "✓ PASS" if result else "✗ FAIL"
        passed += result
        failed += not result
        
        print(f"{status}: {test.name}")
        if not result and test.result:
            if test.result.errors:
                print(f"       Errors: {test.result.errors[0]}")
    
    print()
    print("=" * 70)
    print(f"Results: {passed}/{len(tests)} passed, {failed}/{len(tests)} failed")
    print("=" * 70)
    
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run_tests())
