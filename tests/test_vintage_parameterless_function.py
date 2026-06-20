from pascal1981.compile_to_llvm import compile_to_llvm
from tests.support import parse_source, requires_llvm


@requires_llvm
def test_parameterless_extern_function_identifier_emits_zero_arg_call():
    src = (
        "PROGRAM main;\n"
        "FUNCTION prime_count: INTEGER; EXTERN;\n"
        "VAR count: INTEGER;\n"
        "BEGIN\n"
        "  count := prime_count\n"
        "END.\n"
    )

    ir = compile_to_llvm(parse_source(src))

    assert 'declare external i16 @"prime_count"()' in ir
    assert 'call i16 @"prime_count"()' in ir
