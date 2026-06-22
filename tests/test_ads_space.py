"""ADS memory-spaces: PointerType.space + the SPACE enum.

Types only, no IR.
"""
from pascal1981.builtins_registry import register_builtins
from pascal1981.codegen import Codegen
from pascal1981.symbol_table import SymbolTable
from pascal1981.type_system import REAL_TYPE, EnumType, PointerType


def _ads(space):
    return PointerType(REAL_TYPE, flavor='ADS', space=space)


def test_ads_space_is_part_of_identity():
    # Different concrete spaces are incompatible.
    assert not _ads(1).equivalent_to(_ads(2))  # GLOBAL vs SHARED
    # Same space is equivalent.
    assert _ads(1).equivalent_to(_ads(1))


def test_plain_pointer_wildcard_still_matches():
    plain = PointerType(REAL_TYPE, flavor='POINTER')
    assert plain.equivalent_to(_ads(1))
    assert _ads(1).equivalent_to(plain)


def test_spaceless_ads_unchanged():
    # Existing ADS code carries no space -> defaults None, stays equivalent.
    assert _ads(None).equivalent_to(_ads(None))


def test_space_enum_registered_and_ordinals():
    st = SymbolTable()
    register_builtins(st, features={})
    for name in ('HOST', 'GLOBAL', 'SHARED', 'CONSTANT', 'LOCAL'):
        sym = st.lookup(name)
        assert sym is not None and isinstance(sym.type, EnumType)
        assert sym.type.name == 'SPACE'
    space_type = st.lookup('SPACE')
    assert space_type is not None and space_type.kind == 'type'


def test_space_ordinals_fold_in_codegen():
    cg = Codegen()
    assert cg.constants['HOST'] == 0
    assert cg.constants['GLOBAL'] == 1
    assert cg.constants['SHARED'] == 2
    assert cg.constants['CONSTANT'] == 3
    assert cg.constants['LOCAL'] == 4
