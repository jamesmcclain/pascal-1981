from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence, Union

from .ast_nodes import (AdrExpr, AdsExpr, ArrayType, AssignStmt, ASTNode, Attribute, BinOp, Block, BoolLiteral, BreakStmt, BuiltinType, CaseElement, CaseStmt, CharLiteral,
                        CompoundStmt, ConstDecl, CycleStmt, Declaration, Designator, EmptyStmt, EnumType, Expression, FileType, ForStmt, FuncCall, FuncDecl, GotoStmt, Identifier,
                        IfStmt, ImplementationUnit, IndexRange, InterfaceUnit, IntLiteral, LabelDecl, LabelStmt, LowerExpr, LStringType, ModuleUnit, NamedType, NilLiteral, Param,
                        PointerType, ProcCallStmt, ProcDecl, ProgramUnit, RangeExpr, RealLiteral, RecordType, RepeatStmt, ReturnStmt, RetypeExpr, Selector, SetConstructor, SetType,
                        SizeofExpr, Statement, StringLiteral, SubrangeType, Type, TypeDecl, UnaryOp, UpperExpr, UseClause, ValueDecl, VarDecl, WhileStmt, WithStmt, WriteArg)
from .lexer import ALL_CODES, KEYWORD_CODES, LexerError, Token, lex_file


class ParserError(Exception):
    pass


class Parser:

    def __init__(self, tokens: Sequence[Token]):
        self.tokens = list(tokens)
        self.pos = 0

    def current(self) -> Token:
        return self.tokens[self.pos]

    def next_kind(self, offset: int = 1) -> str:
        index = self.pos + offset
        if index < len(self.tokens):
            return self.tokens[index].kind
        return 'EOF'

    def match(self, kind: str) -> bool:
        if self.current().kind == kind:
            self.pos += 1
            return True
        return False

    def expect(self, kind: str) -> Token:
        tok = self.current()
        if tok.kind != kind:
            self.error(f"expected {kind}, got {tok.kind}")
        self.pos += 1
        return tok

    def error(self, message: str) -> None:
        tok = self.current()
        raise ParserError(f"{message} at line {tok.line}, column {tok.column} (token {tok.kind} {tok.lexeme!r})")

    def parse(self) -> Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit]:
        self.skip_include_directives()
        # Collect *all* leading INTERFACE units that were spliced in via
        # $INCLUDE before the main compilation unit.  A single implementation
        # file may include its own interface header AND one or more dependency
        # interface headers (e.g. GRAPHI + BASEPL before IMPLEMENTATION OF
        # GRAPHICS).  This extends the single-interface path to a
        # loop so arbitrarily many headers are accepted.
        interfaces: List[InterfaceUnit] = []
        while self.current().kind == 'INTERFACE' or self._at_device_prefix('INTERFACE'):
            if self._at_device_prefix('INTERFACE'):
                self.pos += 1  # consume DEVICE
                interfaces.append(self.parse_interface_unit(is_device=True))
            else:
                interfaces.append(self.parse_interface_unit())
            self.skip_include_directives()
            if self.current().kind == 'EOF':
                # Standalone interface file: return it directly.  If somehow
                # multiple bare interfaces were accumulated (unusual), return
                # the first to preserve backward-compatible behaviour.
                return interfaces[0]
        unit = self.parse_compilation_unit()
        for iface in interfaces:
            # For ImplementationUnit, attach the interface whose name matches
            # the unit to unit.interface (for signature validation and
            # device-entry marking).  Name-matching is correct here: with
            # multiple spliced headers, the first one is not necessarily the
            # unit's own interface.
            if isinstance(unit, ImplementationUnit) and unit.interface is None:
                if iface.name.upper() == unit.name.upper():
                    unit.interface = iface
            # All spliced interfaces go into local_interfaces on every unit
            # type so the type checker and codegen can resolve USES from the
            # spliced headers that actually appear in the source file.
            if hasattr(unit, 'local_interfaces'):
                unit.local_interfaces.append(iface)
        if isinstance(unit, ImplementationUnit) and unit.interface is None:
            self.error(f"IMPLEMENTATION OF {unit.name} must include its matching INTERFACE header before the implementation")
        self.skip_include_directives()
        self.expect('EOF')
        return unit

    def parse_compilation_unit(self) -> Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit]:
        if self.current().kind == 'PROGRAM':
            return self.parse_program_unit()
        elif self.current().kind == 'MODULE':
            return self.parse_module_unit()
        elif self._at_device_prefix('MODULE'):
            # Contextual keyword DEVICE preceding MODULE -> a device module.
            self.pos += 1  # consume the contextual DEVICE identifier
            return self.parse_module_unit(is_device=True)
        elif self.current().kind == 'INTERFACE':
            return self.parse_interface_unit()
        elif self._at_device_prefix('INTERFACE'):
            # Contextual keyword DEVICE preceding INTERFACE -> a device interface.
            self.pos += 1
            return self.parse_interface_unit(is_device=True)
        elif self.current().kind == 'IMPLEMENTATION':
            return self.parse_implementation_unit()
        elif self._at_device_prefix('IMPLEMENTATION'):
            # Contextual keyword DEVICE preceding IMPLEMENTATION -> a device implementation.
            self.pos += 1
            return self.parse_implementation_unit(is_device=True)
        else:
            self.error('expected compilation unit start')

    def parse_program_unit(self) -> ProgramUnit:
        self.expect('PROGRAM')
        name = self.expect('IDENTIFIER').lexeme
        params: List[str] = []
        if self.match('LPAREN'):
            params = self.parse_identifier_list()
            self.expect('RPAREN')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        uses: List[UseClause] = []
        while self.current().kind == 'USES':
            uses.extend(self.parse_uses_clause())
            self.skip_include_directives()
        block = self.parse_block()
        self.skip_include_directives()
        self.expect('DOT')
        return ProgramUnit(name, params, uses, block)

    def _at_device_prefix(self, next_kind: str) -> bool:
        """True when the cursor is on a contextual `DEVICE` followed by `next_kind`.

        `DEVICE` is not a reserved word (so vintage code may use it as an
        identifier); it is recognized contextually only immediately before a
        device-marked compilation-unit keyword.
        """
        cur = self.current()
        return (cur.kind == 'IDENTIFIER' and cur.lexeme.upper() == 'DEVICE' and self.next_kind(1) == next_kind)

    def _at_device_module(self) -> bool:
        """Backward-compatible helper for DEVICE MODULE call sites/tests."""
        return self._at_device_prefix('MODULE')

    def parse_module_unit(self, is_device: bool = False) -> ModuleUnit:
        self.expect('MODULE')
        name = self.expect('IDENTIFIER').lexeme
        self.expect('SEMICOLON')
        self.skip_include_directives()
        uses: List[UseClause] = []
        while self.current().kind == 'USES':
            uses.extend(self.parse_uses_clause())
            self.skip_include_directives()
        decls: List[Declaration] = []
        while self.current().kind in self.declaration_starters():
            decls.extend(self.parse_declaration_section())
            self.skip_include_directives()

        # Vintage IBM/MS Pascal modules are "programs without a body" and end
        # with END.  The reimplementation has historically also accepted a
        # shorthand bare-dot terminator for module fixtures.  Accept both, but
        # do not accept a compound statement body: `MODULE M; BEGIN END.` must
        # still fail because the END here is only a terminator after the module
        # declaration part.
        if self.current().kind == 'END':
            self.expect('END')
            self.expect('DOT')
        else:
            self.expect('DOT')
        return ModuleUnit(name, uses, decls, is_device=is_device)

    def parse_interface_unit(self, is_device: bool = False) -> InterfaceUnit:
        self.expect('INTERFACE')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        self.expect('UNIT')
        name = self.expect('IDENTIFIER').lexeme
        params: List[str] = []
        if self.match('LPAREN'):
            params = self.parse_identifier_list()
            self.expect('RPAREN')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        uses: List[UseClause] = []
        while self.current().kind == 'USES':
            uses.extend(self.parse_uses_clause())
            self.skip_include_directives()
        decls: List[Declaration] = []
        while self.current().kind in self.declaration_starters():
            decls.extend(self.parse_interface_declaration())
            self.skip_include_directives()
        # Interface terminator (grammar: {BEGIN}- END ;): exactly one END, optionally
        # preceded by a BEGIN initialization block. The END closes the optional block
        # AND terminates the interface -- there is no second END. The unit is therefore
        # self-delimiting: when include-spliced, this END;/`;` is immediately followed by
        # the IMPLEMENTATION/PROGRAM/MODULE that included it, with no special-casing.
        has_init = False
        if self.current().kind == 'BEGIN':
            has_init = True
            self.parse_compound_statement()  # consumes BEGIN [statements] END
            self.expect('SEMICOLON')
        else:
            self.expect('END')
            self.expect('SEMICOLON')
        return InterfaceUnit(name, params, uses, decls, is_device=is_device, has_init=has_init)

    def parse_implementation_unit(self, is_device: bool = False) -> ImplementationUnit:
        self.expect('IMPLEMENTATION')
        self.expect('OF')
        name = self.expect('IDENTIFIER').lexeme
        self.expect('SEMICOLON')
        self.skip_include_directives()
        uses: List[UseClause] = []
        while self.current().kind == 'USES':
            uses.extend(self.parse_uses_clause())
            self.skip_include_directives()
        decls: List[Declaration] = []
        while self.current().kind in self.declaration_starters():
            decls.extend(self.parse_declaration_section())
            self.skip_include_directives()
        init_body: Optional[List[Statement]] = None
        if self.current().kind == 'BEGIN':
            init_body = self.parse_compound_statement().stmts
        self.skip_include_directives()
        self.expect('DOT')
        return ImplementationUnit(name, uses, decls, init_body, is_device=is_device)

    def parse_uses_clause(self) -> List[UseClause]:
        self.expect('USES')
        clauses: List[UseClause] = []
        clauses.append(self.parse_uses_import())
        while self.match('COMMA'):
            clauses.append(self.parse_uses_import())
        self.match('SEMICOLON')
        return clauses

    def parse_uses_import(self) -> UseClause:
        name = self.expect('IDENTIFIER').lexeme
        imports: Optional[List[str]] = None
        if self.match('LPAREN'):
            imports = self.parse_identifier_list()
            self.expect('RPAREN')
        return UseClause(name, imports)

    def declaration_starters(self) -> set[str]:
        return {'CONST', 'TYPE', 'VAR', 'VALUE', 'LABEL', 'PROCEDURE', 'FUNCTION'}

    def parse_block(self) -> Block:
        self.skip_include_directives()
        decls: List[Declaration] = []
        while self.current().kind in self.declaration_starters():
            decls.extend(self.parse_declaration_section())
            self.skip_include_directives()
        body = self.parse_compound_statement().stmts
        return Block(decls, body)

    def parse_declaration_section(self) -> List[Declaration]:
        self.skip_include_directives()
        kind = self.current().kind
        if kind == 'CONST':
            return self.parse_const_decl()
        elif kind == 'TYPE':
            return self.parse_type_decl()
        elif kind == 'VAR':
            return self.parse_var_decl()
        elif kind == 'VALUE':
            return self.parse_value_decl()
        elif kind == 'LABEL':
            return [self.parse_label_decl()]
        elif kind == 'PROCEDURE':
            return [self.parse_proc_decl()]
        elif kind == 'FUNCTION':
            return [self.parse_func_decl()]
        else:
            self.error('expected declaration section')

    def parse_interface_declaration(self) -> List[Declaration]:
        kind = self.current().kind
        if kind in {'CONST', 'TYPE', 'VAR', 'LABEL'}:
            return self.parse_declaration_section()
        if kind == 'PROCEDURE':
            name, params, attributes = self.parse_proc_decl_header()
            self.expect('SEMICOLON')
            # In an interface, procedures have no body (signature-only)
            return [ProcDecl(name, params, attributes, body=None)]
        if kind == 'FUNCTION':
            name, params, return_type, attributes = self.parse_func_decl_header()
            self.expect('SEMICOLON')
            # In an interface, functions have no body (signature-only)
            return [FuncDecl(name, params, return_type, attributes, body=None)]
        self.error('expected interface declaration')

    def parse_const_decl(self) -> List[ConstDecl]:
        self.expect('CONST')
        decls: List[ConstDecl] = []
        while self.current().kind == 'IDENTIFIER':
            name = self.expect('IDENTIFIER').lexeme
            self.expect('EQ')
            value = self.parse_constant()
            self.expect('SEMICOLON')
            decls.append(ConstDecl(name, value))
        return decls

    def parse_type_decl(self) -> List[TypeDecl]:
        self.expect('TYPE')
        decls: List[TypeDecl] = []
        while self.current().kind == 'IDENTIFIER':
            name = self.expect('IDENTIFIER').lexeme
            self.expect('EQ')
            type_expr = self.parse_type()
            self.expect('SEMICOLON')
            decls.append(TypeDecl(name, type_expr))
        return decls

    def parse_var_decl(self) -> List[VarDecl]:
        self.expect('VAR')
        decls: List[VarDecl] = []
        while self.current().kind == 'IDENTIFIER' or self.current().kind == 'LBRACKET':
            attributes = self.parse_attribute_section_optional()
            names = self.parse_identifier_list()
            self.expect('COLON')
            type_expr = self.parse_type()
            self.expect('SEMICOLON')
            decls.append(VarDecl(names, type_expr, attributes, meta_flags=dict(self.current_flags())))
        return decls

    def parse_value_decl(self) -> List[ValueDecl]:
        self.expect('VALUE')
        decls: List[ValueDecl] = []
        while self.current().kind == 'IDENTIFIER':
            target = self.parse_designator()
            if self.current().kind in {'EQ', 'ASSIGN'}:
                self.pos += 1
            else:
                self.error('expected = or := in value declaration')
            value = self.parse_value_initializer()
            self.expect('SEMICOLON')
            decls.append(ValueDecl(target, value))
        return decls

    def parse_value_initializer(self) -> Expression:
        """Parse a VALUE-section constant initializer.

        IBM Pascal accepts set constants such as [] in VALUE declarations.
        Keep this narrower than a general expression so VALUE does not silently
        admit arbitrary runtime expressions.
        """
        if self.current().kind == 'LBRACKET':
            self.pos += 1
            elements: List[Expression] = []
            if self.current().kind != 'RBRACKET':
                elements.append(self.parse_set_element())
                while self.match('COMMA'):
                    elements.append(self.parse_set_element())
            self.expect('RBRACKET')
            return SetConstructor(elements)
        return self.parse_constant()

    def parse_label_decl(self) -> LabelDecl:
        self.expect('LABEL')
        labels: List[Union[int, str]] = []
        labels.append(self.parse_label_id())
        while self.match('COMMA'):
            labels.append(self.parse_label_id())
        self.expect('SEMICOLON')
        return LabelDecl(labels)

    def parse_proc_decl(self) -> ProcDecl:
        name, params, attributes = self.parse_proc_decl_header()
        self.expect('SEMICOLON')
        body: Optional[Block] = None
        directive: Optional[str] = None
        if self.current().kind in {'EXTERN', 'EXTERNAL', 'FORWARD'}:
            directive = self.current().kind
            self.pos += 1
            self.expect('SEMICOLON')
        else:
            body = self.parse_block()
            self.expect('SEMICOLON')
        return ProcDecl(name, params, attributes, body, directive)

    def parse_func_decl(self) -> FuncDecl:
        name, params, return_type, attributes = self.parse_func_decl_header()
        self.expect('SEMICOLON')
        body: Optional[Block] = None
        directive: Optional[str] = None
        if self.current().kind in {'EXTERN', 'EXTERNAL', 'FORWARD'}:
            directive = self.current().kind
            self.pos += 1
            self.expect('SEMICOLON')
        else:
            body = self.parse_block()
            self.expect('SEMICOLON')
        return FuncDecl(name, params, return_type, attributes, body, directive)

    def parse_proc_decl_header(self) -> tuple[str, List[Param], List[str]]:
        self.expect('PROCEDURE')
        name = self.expect('IDENTIFIER').lexeme
        params: List[Param] = []
        if self.match('LPAREN'):
            params = self.parse_parameter_list()
            self.expect('RPAREN')
        attributes = self.parse_attribute_section_optional()
        return name, params, attributes

    def parse_func_decl_header(self) -> tuple[str, List[Param], Type, List[str]]:
        self.expect('FUNCTION')
        name = self.expect('IDENTIFIER').lexeme
        params: List[Param] = []
        if self.match('LPAREN'):
            params = self.parse_parameter_list()
            self.expect('RPAREN')
        self.expect('COLON')
        return_type = self.parse_type()
        attributes = self.parse_attribute_section_optional()
        return name, params, return_type, attributes

    def parse_parameter_list(self) -> List[Param]:
        params: List[Param] = []
        params.append(self.parse_parameter_group())
        while self.match('SEMICOLON'):
            if self.current().kind == 'RPAREN':
                break
            params.append(self.parse_parameter_group())
        return params

    def parse_parameter_group(self) -> Param:
        mode: Optional[str] = None
        if self.current().kind in {'VAR', 'VARS', 'CONST', 'CONSTS'}:
            mode = self.current().kind
            self.pos += 1
        names = self.parse_identifier_list()
        self.expect('COLON')
        type_expr = self.parse_type()
        return Param(mode, names, type_expr)

    def parse_attribute_section_optional(self) -> List[Attribute]:
        attributes: List[Attribute] = []
        if not self.match('LBRACKET'):
            return attributes
        if self.current().kind != 'RBRACKET':
            attributes.append(self.parse_attribute_item())
            while self.match('COMMA'):
                attributes.append(self.parse_attribute_item())
        self.expect('RBRACKET')
        return attributes

    def parse_attribute_item(self) -> Attribute:
        # Bare-keyword attributes: the six confirmed storage attributes.
        if self.current().kind in {'READONLY', 'PUBLIC', 'STATIC', 'EXTERNAL', 'EXTERN', 'PURE'}:
            attr = self.current().kind
            self.pos += 1
            return Attribute(attr)
        # SPACE(constant): the first parameterized attribute (residence of
        # storage). `SPACE` is contextual -- recognized from an IDENTIFIER
        # lexeme, not lexer-reserved -- so vintage `space` identifiers survive.
        cur = self.current()
        if cur.kind == 'IDENTIFIER' and cur.lexeme.upper() == 'SPACE':
            self.pos += 1
            self.expect('LPAREN')
            arg = self.parse_expression()
            self.expect('RPAREN')
            return Attribute('SPACE', arg)
        # C / CDECL: foreign C-ABI marker (Phase 1 of the C-FFI plan,
        # docs/c-abi-foreign-functions.md). Contextual like SPACE -- recognized
        # from an IDENTIFIER lexeme, never lexer-reserved -- so vintage `c`/`cdecl`
        # identifiers survive. Both spellings normalize to 'C'. It opts a foreign
        # routine into C-ABI-correct lowering; for scalar/pointer signatures the
        # current lowering is already correct, so the marker is presently inert
        # except that it parses. By-value aggregates remain rejected until the
        # aggregate classifier (Phase 2) ships.
        if cur.kind == 'IDENTIFIER' and cur.lexeme.upper() in {'C', 'CDECL'}:
            self.pos += 1
            return Attribute('C')
        self.error('expected attribute item')

    def parse_compound_statement(self) -> CompoundStmt:
        self.expect('BEGIN')
        stmts: List[Statement] = []
        if self.current().kind != 'END':
            stmts.append(self.parse_statement())
            while self.match('SEMICOLON'):
                if self.current().kind == 'END':
                    break
                stmts.append(self.parse_statement())
        self.expect('END')
        return CompoundStmt(stmts)

    def current_flags(self) -> dict[str, bool]:
        return dict(getattr(self.current(), 'flags', {}))

    def parse_statement(self) -> Statement:
        kind = self.current().kind
        flags = self.current_flags()
        if kind == 'BEGIN':
            return self.parse_compound_statement()
        if kind == 'IF':
            return self.parse_if_statement()
        if kind == 'FOR':
            return self.parse_for_statement()
        if kind == 'REPEAT':
            return self.parse_repeat_statement()
        if kind == 'WHILE':
            return self.parse_while_statement()
        if kind == 'CASE':
            return self.parse_case_statement()
        if kind == 'WITH':
            return self.parse_with_statement()
        if kind == 'GOTO':
            self.pos += 1
            label = self.parse_label_id()
            return GotoStmt(label)
        if kind == 'RETURN':
            self.pos += 1
            return ReturnStmt()
        if kind == 'BREAK':
            self.pos += 1
            label = self.parse_optional_label_id()
            return BreakStmt(label)
        if kind == 'CYCLE':
            self.pos += 1
            label = self.parse_optional_label_id()
            return CycleStmt(label)
        if kind in {'INTEGER_LITERAL', 'IDENTIFIER'} and self.next_kind() == 'COLON':
            return self.parse_label_statement()
        if kind == 'IDENTIFIER':
            return self.parse_assignment_or_proc_call()
        if kind in {'SEMICOLON', 'END', 'UNTIL', 'ELSE', 'OTHERWISE', 'RPAREN'}:
            return EmptyStmt()
        self.error('expected statement')

    def parse_assignment_or_proc_call(self) -> Statement:
        flags = self.current_flags()
        name = self.expect('IDENTIFIER').lexeme
        selectors: List[Selector] = []
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            selectors.extend(self.parse_selector())

        if self.current().kind == 'ASSIGN':
            self.pos += 1
            expr = self.parse_expression()
            target = Designator(name, selectors)
            return AssignStmt(target, expr, rangeck=flags.get('RANGECK', True), meta_flags=dict(flags))

        if selectors:
            self.error('designator statement must be an assignment')

        args: List[Union[Expression, WriteArg]] = []
        if self.current().kind == 'LPAREN':
            self.pos += 1
            if self.current().kind != 'RPAREN':
                if name.upper() in {'WRITE', 'WRITELN'}:
                    args = self.parse_write_actual_parameter_list()
                else:
                    args = self.parse_actual_parameter_list()
            self.expect('RPAREN')
        # Bare procedure call is allowed.
        return ProcCallStmt(name, args, rangeck=flags.get('RANGECK', True), meta_flags=dict(flags))

    def parse_actual_parameter_list(self) -> List[Expression]:
        exprs: List[Expression] = []
        exprs.append(self.parse_actual_parameter())
        while self.match('COMMA'):
            exprs.append(self.parse_actual_parameter())
        return exprs

    def parse_actual_parameter(self) -> Expression:
        return self.parse_expression()

    def parse_write_actual_parameter_list(self) -> List[WriteArg]:
        args: List[WriteArg] = []
        args.append(self.parse_write_actual_parameter())
        while self.match('COMMA'):
            args.append(self.parse_write_actual_parameter())
        return args

    def parse_write_actual_parameter(self) -> WriteArg:
        expr = self.parse_expression()
        width: Optional[Expression] = None
        precision: Optional[Expression] = None
        if self.match('COLON'):
            if self.current().kind == 'COLON':
                # P::N (manual 12-17): width M omitted — "same as passing
                # MAXINT", i.e. the type's default width is used.  Vintage
                # compiler accepts this.
                self.match('COLON')
                precision = self.parse_expression()
            else:
                width = self.parse_expression()
                if self.match('COLON'):
                    precision = self.parse_expression()
        return WriteArg(expr, width, precision)

    def parse_if_statement(self) -> IfStmt:
        self.expect('IF')
        cond = self.parse_boolean_expression()
        self.expect('THEN')
        then_branch = self.parse_statement()
        else_branch: Optional[Statement] = None
        if self.match('ELSE'):
            else_branch = self.parse_statement()
        return IfStmt(cond, then_branch, else_branch)

    def parse_for_statement(self) -> ForStmt:
        self.expect('FOR')
        static = self.match('STATIC')
        var = self.expect('IDENTIFIER').lexeme
        self.expect('ASSIGN')
        start = self.parse_expression()
        if self.current().kind in {'TO', 'DOWNTO'}:
            direction = self.current().kind
            self.pos += 1
        else:
            self.error('expected TO or DOWNTO')
        end = self.parse_expression()
        self.expect('DO')
        body = self.parse_statement()
        return ForStmt(var, start, end, direction, body, static)

    def parse_repeat_statement(self) -> RepeatStmt:
        self.expect('REPEAT')
        stmts: List[Statement] = []
        if self.current().kind != 'UNTIL':
            stmts.append(self.parse_statement())
            while self.match('SEMICOLON'):
                if self.current().kind == 'UNTIL':
                    break
                stmts.append(self.parse_statement())
        self.expect('UNTIL')
        cond = self.parse_boolean_expression()
        return RepeatStmt(stmts, cond)

    def parse_while_statement(self) -> WhileStmt:
        self.expect('WHILE')
        cond = self.parse_boolean_expression()
        self.expect('DO')
        body = self.parse_statement()
        return WhileStmt(cond, body)

    def parse_case_statement(self) -> CaseStmt:
        self.expect('CASE')
        expr = self.parse_expression()
        self.expect('OF')
        elements: List[CaseElement] = []
        if self.current().kind != 'END':
            elements.append(self.parse_case_element())
            while self.match('SEMICOLON'):
                if self.current().kind in {'OTHERWISE', 'END'}:
                    break
                elements.append(self.parse_case_element())
        otherwise: Optional[Statement] = None
        if self.match('OTHERWISE'):
            otherwise = self.parse_statement()
        self.expect('END')
        return CaseStmt(expr, elements, otherwise, rangeck=self.current_flags().get('RANGECK', True), meta_flags=dict(self.current_flags()))

    def parse_case_element(self) -> CaseElement:
        constants = self.parse_case_constant_list()
        self.expect('COLON')
        stmt = self.parse_statement()
        return CaseElement(constants, stmt)

    def parse_case_constant_list(self) -> List[Expression]:
        exprs: List[Expression] = []
        exprs.append(self.parse_case_constant())
        while self.match('COMMA'):
            exprs.append(self.parse_case_constant())
        return exprs

    def parse_case_constant(self) -> Expression:
        expr = self.parse_constant()
        if self.match('RANGE'):
            high = self.parse_constant()
            return RangeExpr(expr, high)
        return expr

    def parse_with_statement(self) -> WithStmt:
        self.expect('WITH')
        targets: List[Designator] = []
        targets.append(self.parse_with_target())
        while self.match('COMMA'):
            targets.append(self.parse_with_target())
        self.expect('DO')
        body = self.parse_statement()
        return WithStmt(targets, body)

    def parse_with_target(self) -> Designator:
        name = self.expect('IDENTIFIER').lexeme
        selectors: List[Selector] = []
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            selectors.extend(self.parse_selector())
        return Designator(name, selectors)

    def parse_label_statement(self) -> LabelStmt:
        label = self.parse_label_id()
        self.expect('COLON')
        stmt = self.parse_statement()
        return LabelStmt(label, stmt)

    def parse_selector(self) -> List[Selector]:
        kind = self.current().kind
        if kind == 'LBRACKET':
            self.pos += 1
            selectors: List[Selector] = [Selector('INDEX', self.parse_expression())]
            while self.match('COMMA'):
                selectors.append(Selector('INDEX', self.parse_expression()))
            self.expect('RBRACKET')
            return selectors
        if kind == 'DOT':
            self.pos += 1
            field = self.expect('IDENTIFIER').lexeme
            return [Selector('FIELD', field)]
        if kind == 'POINTER':
            self.pos += 1
            return [Selector('DEREF', None)]
        self.error('expected selector')

    def parse_boolean_expression(self) -> Expression:
        left = self.parse_expression()
        while (self.current().kind == 'AND' and self.next_kind() == 'THEN') or \
              (self.current().kind == 'OR' and self.next_kind() == 'ELSE'):
            if self.current().kind == 'AND':
                op = 'AND_THEN'
                self.pos += 2
            else:
                op = 'OR_ELSE'
                self.pos += 2
            right = self.parse_expression()
            left = BinOp(op, left, right)
        return left

    def parse_expression(self) -> Expression:
        left = self.parse_simple_expression()
        if self.current().kind in {'EQ', 'NEQ', 'LT', 'LE', 'GT', 'GE', 'IN'}:
            op = self.current().kind
            self.pos += 1
            right = self.parse_simple_expression()
            return BinOp(op, left, right)
        return left

    def parse_simple_expression(self) -> Expression:
        sign: Optional[str] = None
        if self.current().kind in {'PLUS', 'MINUS'}:
            sign = self.current().kind
            self.pos += 1
        left = self.parse_term()
        if sign == 'MINUS':
            left = UnaryOp('MINUS', left)
        while self.current().kind in {'PLUS', 'MINUS', 'OR', 'XOR'}:
            if self.current().kind == 'OR' and self.next_kind() == 'ELSE':
                break
            op = self.current().kind
            self.pos += 1
            right = self.parse_term()
            left = BinOp(op, left, right)
        return left

    def parse_term(self) -> Expression:
        left = self.parse_factor()
        while self.current().kind in {'MUL', 'SLASH', 'DIV', 'MOD', 'AND'}:
            if self.current().kind == 'AND' and self.next_kind() == 'THEN':
                break
            op = self.current().kind
            self.pos += 1
            right = self.parse_factor()
            left = BinOp(op, left, right)
        return left

    def parse_factor(self) -> Expression:
        kind = self.current().kind
        if kind == 'NOT':
            self.pos += 1
            operand = self.parse_factor()
            return UnaryOp('NOT', operand)
        if kind == 'IDENTIFIER':
            name = self.current().lexeme
            if name.upper() == 'RETYPE' and self.next_kind() == 'LPAREN':
                self.pos += 2  # consume 'RETYPE' and '('
                type_id = self.expect('IDENTIFIER').lexeme
                self.expect('COMMA')
                expr = self.parse_expression()
                self.expect('RPAREN')
                selectors = []
                while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
                    selectors.extend(self.parse_selector())
                return RetypeExpr(type_id, expr, selectors)
            elif self.next_kind() == 'LPAREN':
                self.pos += 1
                self.pos += 1
                args: List[Expression] = []
                if self.current().kind != 'RPAREN':
                    if name.upper() in {'WRITE', 'WRITELN', 'ENCODE', 'DECODE'}:
                        args = self.parse_write_actual_parameter_list()
                    else:
                        args = self.parse_actual_parameter_list()
                self.expect('RPAREN')
                return FuncCall(name, args)
            elif self.next_kind() == 'LBRACKET' and self.bracket_payload_contains_range(self.pos + 1):
                self.pos += 1  # consume type identifier
                self.expect('LBRACKET')
                elements: List[Expression] = []
                if self.current().kind != 'RBRACKET':
                    elements.append(self.parse_set_element())
                    while self.match('COMMA'):
                        elements.append(self.parse_set_element())
                self.expect('RBRACKET')
                return SetConstructor(elements, name)
            else:
                self.pos += 1  # consume IDENTIFIER
                designator = self.parse_designator_rest(name)
                return designator
        if kind == 'INTEGER_LITERAL':
            # The lexer already computed the integer value, handling decimal
            # and radix (n#digits) forms uniformly.
            value = self.current().value
            self.pos += 1
            return IntLiteral(value)
        if kind == 'REAL_LITERAL':
            value = float(self.current().lexeme)
            self.pos += 1
            return RealLiteral(value)
        if kind == 'CHAR_LITERAL':
            value = self.current().value
            self.pos += 1
            return CharLiteral(value)
        if kind == 'STRING_LITERAL':
            value = self.current().lexeme
            self.pos += 1
            return StringLiteral(value)
        if kind == 'BOOLEAN_LITERAL':
            value = self.current().lexeme.upper() == 'TRUE'
            self.pos += 1
            return BoolLiteral(value)
        if kind == 'NIL':
            self.pos += 1
            return NilLiteral()
        if kind == 'LPAREN':
            self.pos += 1
            expr = self.parse_expression()
            self.expect('RPAREN')
            return expr
        if kind == 'ADR':
            self.pos += 1
            name = self.expect('IDENTIFIER').lexeme
            return AdrExpr(name)
        if kind == 'ADS':
            self.pos += 1
            name = self.expect('IDENTIFIER').lexeme
            return AdsExpr(name)
        if kind == 'SIZEOF':
            self.pos += 1
            self.expect('LPAREN')
            target: Union[str, Type]
            if self.current().kind == 'IDENTIFIER':
                target = self.current().lexeme
                self.pos += 1
            else:
                target = self.parse_type()
            self.expect('RPAREN')
            return SizeofExpr(target)
        if kind == 'UPPER':
            self.pos += 1
            self.expect('LPAREN')
            name = self.expect('IDENTIFIER').lexeme
            self.expect('RPAREN')
            return UpperExpr(name)
        if kind == 'LOWER':
            self.pos += 1
            self.expect('LPAREN')
            name = self.expect('IDENTIFIER').lexeme
            self.expect('RPAREN')
            return LowerExpr(name)
        if kind == 'LBRACKET':
            self.pos += 1
            elements: List[Expression] = []
            if self.current().kind != 'RBRACKET':
                elements.append(self.parse_set_element())
                while self.match('COMMA'):
                    elements.append(self.parse_set_element())
            self.expect('RBRACKET')
            return SetConstructor(elements)
        self.error('expected factor')

    def bracket_payload_contains_range(self, lbracket_pos: int) -> bool:
        """Return True when a bracketed IDENTIFIER[...] payload contains '..'.

        This conservatively disambiguates typed set constants from array
        indexing without symbol information in the parser.
        """
        depth = 0
        for i in range(lbracket_pos, len(self.tokens)):
            kind = self.tokens[i].kind
            if kind == 'LBRACKET':
                depth += 1
            elif kind == 'RBRACKET':
                depth -= 1
                if depth == 0:
                    return False
            elif kind == 'RANGE' and depth == 1:
                return True
        return False

    def parse_designator_rest(self, name: str) -> Expression:
        """Continue parsing a designator or return as identifier."""
        selectors: List[Selector] = []
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            selectors.extend(self.parse_selector())
        if selectors:
            return Designator(name, selectors)
        else:
            return Identifier(name)

    def parse_designator(self) -> Designator:
        name = self.expect('IDENTIFIER').lexeme
        selectors: List[Selector] = []
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            selectors.extend(self.parse_selector())
        return Designator(name, selectors)

    def parse_constant(self) -> Expression:
        kind = self.current().kind
        if kind == 'INTEGER_LITERAL':
            value = self.current().value
            self.pos += 1
            return IntLiteral(value)
        if kind == 'REAL_LITERAL':
            value = float(self.current().lexeme)
            self.pos += 1
            return RealLiteral(value)
        if kind == 'CHAR_LITERAL':
            value = self.current().value
            self.pos += 1
            return CharLiteral(value)
        if kind == 'STRING_LITERAL':
            value = self.current().lexeme
            self.pos += 1
            return StringLiteral(value)
        if kind == 'BOOLEAN_LITERAL':
            value = self.current().lexeme.upper() == 'TRUE'
            self.pos += 1
            return BoolLiteral(value)
        if kind == 'NIL':
            self.pos += 1
            return NilLiteral()
        if kind == 'IDENTIFIER':
            name = self.current().lexeme
            self.pos += 1
            # WRD(x) and BYWORD(hi,lo) may appear as constant expressions
            # (manual p.6-5, p.11-8); parse them as FuncCall nodes so the
            # constant-folder in codegen can evaluate them at compile time.
            if name.upper() in {'WRD', 'BYWORD'} and self.current().kind == 'LPAREN':
                self.pos += 1  # consume '('
                args = [self.parse_constant()]
                while self.current().kind == 'COMMA':
                    self.pos += 1
                    args.append(self.parse_constant())
                self.expect('RPAREN')
                return FuncCall(name=name, args=args)
            return Identifier(name)
        if kind in {'PLUS', 'MINUS'}:
            sign = self.current().kind
            self.pos += 1
            if self.current().kind == 'INTEGER_LITERAL':
                value = self.current().value
                if sign == 'MINUS':
                    value = -value
                self.pos += 1
                return IntLiteral(value)
            if self.current().kind == 'REAL_LITERAL':
                value = float(self.current().lexeme)
                if sign == 'MINUS':
                    value = -value
                self.pos += 1
                return RealLiteral(value)
            self.error('expected numeric constant')
        self.error('expected constant')

    def parse_type(self) -> Type:
        packed = self.match('PACKED')
        kind = self.current().kind
        if kind in {'ARRAY', 'SUPER'}:
            is_super = kind == 'SUPER'
            if is_super:
                self.pos += 1
                self.expect('ARRAY')
            else:
                self.pos += 1
            self.expect('LBRACKET')
            index_range = self.parse_index_range(allow_star=is_super)
            self.expect('RBRACKET')
            self.expect('OF')
            element_type = self.parse_type()
            return ArrayType(index_range, element_type, packed, is_super)
        if kind == 'RECORD':
            self.pos += 1
            fields: List[tuple[List[str], Type]] = []
            while self.current().kind != 'END':
                names = self.parse_identifier_list()
                self.expect('COLON')
                field_type = self.parse_type()
                fields.append((names, field_type))
                if self.current().kind == 'SEMICOLON':
                    self.pos += 1
                else:
                    break
            self.expect('END')
            return RecordType(fields, packed)
        if kind == 'SET':
            self.pos += 1
            self.expect('OF')
            base = self.parse_set_base()
            return SetType(base)
        if kind == 'FILE':
            self.pos += 1
            self.expect('OF')
            element_type = self.parse_type()
            return FileType(element_type)
        if kind == 'LPAREN':
            self.pos += 1
            values = self.parse_identifier_list()
            self.expect('RPAREN')
            return EnumType(values)
        if kind == 'LSTRING':
            self.pos += 1
            self.expect('LPAREN')
            max_len_expr = self.parse_constant()
            self.expect('RPAREN')
            # Extract integer value from expression
            if isinstance(max_len_expr, IntLiteral):
                max_len = max_len_expr.value
            else:
                max_len = 256  # fallback
            return LStringType(max_len)
        if kind == 'POINTER':
            self.pos += 1
            base = self.parse_type()
            return PointerType(base, 'POINTER')
        if kind == 'ADR':
            self.pos += 1
            self.expect('OF')
            base = self.parse_type()
            return PointerType(base, 'ADR')
        if kind == 'ADS':
            self.pos += 1
            # Optional parameterized pointee space: ADS(constant) OF T.
            space = None
            if self.match('LPAREN'):
                space = self.parse_expression()
                self.expect('RPAREN')
            self.expect('OF')
            base = self.parse_type()
            return PointerType(base, 'ADS', space=space)
        if kind == 'IDENTIFIER':
            name = self.current().lexeme
            self.pos += 1
            param: Optional[Union[int, str]] = None
            if self.match('LPAREN'):
                param_expr = self.parse_constant()
                self.expect('RPAREN')
                if isinstance(param_expr, IntLiteral):
                    param = param_expr.value
                elif isinstance(param_expr, Identifier):
                    param = param_expr.name
            return NamedType(name, param)
        if kind in {'INTEGER', 'REAL', 'BOOLEAN', 'CHAR', 'WORD', 'ADRMEM'}:
            name = self.current().kind
            self.pos += 1
            return BuiltinType(name)
        self.error('expected type')

    def parse_index_range(self, allow_star: bool = False) -> IndexRange:
        low = self.parse_constant()
        self.expect('RANGE')
        high: Optional[Expression] = None
        if allow_star:
            self.expect('MUL')
            high = None
        else:
            high = self.parse_constant()
        return IndexRange(low, high)

    def parse_set_base(self) -> Type:
        """Parse a set base type: a named/builtin ordinal type, or a subrange.

        Subranges (`SET OF 1..10`, `SET OF 'A'..'Z'`, `SET OF lo..hi`) preserve
        both bounds in a SubrangeType rather than collapsing to the host type,
        so the declared range survives into the AST.
        """
        # Named type, possibly the low end of a subrange (`color` or `red..blue`).
        if self.current().kind == 'IDENTIFIER':
            if self.next_kind() == 'RANGE':
                low_expr = self.parse_constant()
                self.expect('RANGE')
                high_expr = self.parse_constant()
                return SubrangeType(low_expr, high_expr, self._subrange_host(low_expr, high_expr))
            name = self.current().lexeme
            self.pos += 1
            return NamedType(name, None)
        # Literal, possibly the low end of a subrange (`1..10`, `'A'..'Z'`).
        if self.current().kind in {'INTEGER_LITERAL', 'REAL_LITERAL', 'CHAR_LITERAL', 'STRING_LITERAL', 'BOOLEAN_LITERAL'}:
            expr = self.parse_constant()
            if self.match('RANGE'):
                high_expr = self.parse_constant()
                return SubrangeType(expr, high_expr, self._subrange_host(expr, high_expr))
            # A bare literal as a set base is unusual; treat it as the host type.
            host = self._subrange_host(expr, expr)
            return BuiltinType(host if host else 'INTEGER')
        if self.current().kind in {'INTEGER', 'REAL', 'BOOLEAN', 'CHAR', 'WORD', 'ADRMEM'}:
            name = self.current().kind
            self.pos += 1
            return BuiltinType(name)
        self.error('expected set base type or range')

    @staticmethod
    def _subrange_host(low: Expression, high: Expression) -> Optional[str]:
        """Best-effort host ordinal type for a subrange, inferred from its bound
        literals. Returns None when the bounds are named constants/identifiers
        whose type can't be known at parse time (resolved later by the type
        checker)."""
        for ordinal, literal in (('INTEGER', IntLiteral), ('CHAR', CharLiteral), ('BOOLEAN', BoolLiteral)):
            if isinstance(low, literal) and isinstance(high, literal):
                return ordinal
        return None

    def parse_set_element(self) -> Expression:
        expr = self.parse_expression()
        if self.match('RANGE'):
            high = self.parse_expression()
            return RangeExpr(expr, high)
        return expr

    def parse_identifier_list(self) -> List[str]:
        names: List[str] = []
        names.append(self.expect('IDENTIFIER').lexeme)
        while self.match('COMMA'):
            names.append(self.expect('IDENTIFIER').lexeme)
        return names

    def parse_optional_label_id(self) -> Optional[Union[int, str]]:
        if self.current().kind in {'INTEGER_LITERAL', 'IDENTIFIER'}:
            return self.parse_label_id()
        return None

    def parse_label_id(self) -> Union[int, str]:
        if self.current().kind == 'INTEGER_LITERAL':
            value = int(self.current().lexeme)
            self.pos += 1
            return value
        if self.current().kind == 'IDENTIFIER':
            name = self.current().lexeme
            self.pos += 1
            return name
        self.error('expected label id')

    def skip_include_directives(self) -> None:
        while self.current().kind == 'INCLUDE_DIRECTIVE':
            self.pos += 1

    def advance_end_semicolon(self) -> None:
        self.expect('END')
        self.expect('SEMICOLON')


def parse_file(path: str) -> Union[ProgramUnit, ModuleUnit, InterfaceUnit, ImplementationUnit]:
    """Parse a Pascal source file and return the AST root node."""
    tokens = lex_file(path)
    return Parser(tokens).parse()


def main() -> int:
    parser = argparse.ArgumentParser(description="Pascal Parser Driver.")
    parser.add_argument("source_file", type=str, help="The Pascal source file to parse (e.g., program.pas).")
    args = parser.parse_args()

    source_file = args.source_file
    try:
        ast = parse_file(source_file)
        print(f'OK: Parsed as {type(ast).__name__}')
        return 0
    except (LexerError, ParserError) as exc:
        print(f'Parse error: {exc}', file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f'File not found: {source_file}', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'System file error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
