from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence

from lexer import (
    ALL_CODES,
    KEYWORD_CODES,
    LexerError,
    Token,
    lex_file,
)


class ParserError(Exception):
    pass


@dataclass
class Node:
    kind: str
    children: List[object]


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

    def parse(self) -> None:
        self.skip_include_directives()
        self.parse_compilation_unit()
        self.skip_include_directives()
        self.expect('EOF')

    def parse_compilation_unit(self) -> None:
        if self.current().kind == 'PROGRAM':
            self.parse_program_unit()
        elif self.current().kind == 'MODULE':
            self.parse_module_unit()
        elif self.current().kind == 'INTERFACE':
            self.parse_interface_unit()
        elif self.current().kind == 'IMPLEMENTATION':
            self.parse_implementation_unit()
        else:
            self.error('expected compilation unit start')

    def parse_program_unit(self) -> None:
        self.expect('PROGRAM')
        self.expect('IDENTIFIER')
        if self.match('LPAREN'):
            self.parse_identifier_list()
            self.expect('RPAREN')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        while self.current().kind == 'USES':
            self.parse_uses_clause()
            self.skip_include_directives()
        self.parse_block()
        self.skip_include_directives()
        self.expect('DOT')

    def parse_module_unit(self) -> None:
        self.expect('MODULE')
        self.expect('IDENTIFIER')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        while self.current().kind == 'USES':
            self.parse_uses_clause()
            self.skip_include_directives()
        while self.current().kind in self.declaration_starters():
            self.parse_declaration_section()
            self.skip_include_directives()
        self.expect('DOT')

    def parse_interface_unit(self) -> None:
        self.expect('INTERFACE')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        self.expect('UNIT')
        self.expect('IDENTIFIER')
        if self.match('LPAREN'):
            self.parse_identifier_list()
            self.expect('RPAREN')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        while self.current().kind in self.declaration_starters():
            self.parse_interface_declaration()
            self.skip_include_directives()
        self.advance_end_semicolon()

    def parse_implementation_unit(self) -> None:
        self.expect('IMPLEMENTATION')
        self.expect('OF')
        self.expect('IDENTIFIER')
        self.expect('SEMICOLON')
        self.skip_include_directives()
        while self.current().kind == 'USES':
            self.parse_uses_clause()
            self.skip_include_directives()
        while self.current().kind in self.declaration_starters():
            self.parse_declaration_section()
            self.skip_include_directives()
        if self.current().kind == 'BEGIN':
            self.parse_compound_statement()
        elif self.current().kind == 'END':
            self.pos += 1
        self.skip_include_directives()
        self.expect('DOT')

    def parse_uses_clause(self) -> None:
        self.expect('USES')
        self.expect('IDENTIFIER')
        while self.match('COMMA'):
            self.expect('IDENTIFIER')
        if self.match('LPAREN'):
            self.parse_identifier_list()
            self.expect('RPAREN')
        self.expect('SEMICOLON')

    def declaration_starters(self) -> set[str]:
        return {'CONST', 'TYPE', 'VAR', 'VALUE', 'LABEL', 'PROCEDURE', 'FUNCTION'}

    def parse_block(self) -> None:
        self.skip_include_directives()
        while self.current().kind in self.declaration_starters():
            self.parse_declaration_section()
            self.skip_include_directives()
        self.parse_compound_statement()

    def parse_declaration_section(self) -> None:
        self.skip_include_directives()
        kind = self.current().kind
        if kind == 'CONST':
            self.parse_const_decl()
        elif kind == 'TYPE':
            self.parse_type_decl()
        elif kind == 'VAR':
            self.parse_var_decl()
        elif kind == 'VALUE':
            self.parse_value_decl()
        elif kind == 'LABEL':
            self.parse_label_decl()
        elif kind == 'PROCEDURE':
            self.parse_proc_decl()
        elif kind == 'FUNCTION':
            self.parse_func_decl()
        else:
            self.error('expected declaration section')

    def parse_interface_declaration(self) -> None:
        kind = self.current().kind
        if kind in {'CONST', 'TYPE', 'VAR', 'LABEL'}:
            self.parse_declaration_section()
            return
        if kind == 'PROCEDURE':
            self.parse_proc_decl_header()
            self.expect('SEMICOLON')
            return
        if kind == 'FUNCTION':
            self.parse_func_decl_header()
            self.expect('SEMICOLON')
            return
        self.error('expected interface declaration')

    def parse_const_decl(self) -> None:
        self.expect('CONST')
        while self.current().kind == 'IDENTIFIER':
            self.expect('IDENTIFIER')
            self.expect('EQ')
            self.parse_constant()
            self.expect('SEMICOLON')

    def parse_type_decl(self) -> None:
        self.expect('TYPE')
        while self.current().kind == 'IDENTIFIER':
            self.expect('IDENTIFIER')
            self.expect('EQ')
            self.parse_type()
            self.expect('SEMICOLON')

    def parse_var_decl(self) -> None:
        self.expect('VAR')
        while self.current().kind == 'IDENTIFIER' or self.current().kind == 'LBRACKET':
            self.parse_attribute_section_optional()
            self.parse_identifier_list()
            self.expect('COLON')
            self.parse_type()
            self.expect('SEMICOLON')

    def parse_value_decl(self) -> None:
        self.expect('VALUE')
        while self.current().kind == 'IDENTIFIER':
            self.expect('IDENTIFIER')
            if self.current().kind in {'EQ', 'ASSIGN'}:
                self.pos += 1
            else:
                self.error('expected = or := in value declaration')
            self.parse_constant()
            self.expect('SEMICOLON')

    def parse_label_decl(self) -> None:
        self.expect('LABEL')
        self.parse_label_id()
        while self.match('COMMA'):
            self.parse_label_id()
        self.expect('SEMICOLON')

    def parse_proc_decl(self) -> None:
        self.parse_proc_decl_header()
        self.expect('SEMICOLON')
        if self.current().kind in {'EXTERN', 'EXTERNAL', 'FORWARD'}:
            self.pos += 1
            self.expect('SEMICOLON')
            return
        self.parse_block()
        self.expect('SEMICOLON')

    def parse_func_decl(self) -> None:
        self.parse_func_decl_header()
        self.expect('SEMICOLON')
        if self.current().kind in {'EXTERN', 'EXTERNAL', 'FORWARD'}:
            self.pos += 1
            self.expect('SEMICOLON')
            return
        self.parse_block()
        self.expect('SEMICOLON')

    def parse_proc_decl_header(self) -> None:
        self.expect('PROCEDURE')
        self.expect('IDENTIFIER')
        if self.match('LPAREN'):
            self.parse_parameter_list()
            self.expect('RPAREN')
        self.parse_attribute_section_optional()

    def parse_func_decl_header(self) -> None:
        self.expect('FUNCTION')
        self.expect('IDENTIFIER')
        if self.match('LPAREN'):
            self.parse_parameter_list()
            self.expect('RPAREN')
        self.expect('COLON')
        self.parse_type()
        self.parse_attribute_section_optional()

    def parse_parameter_list(self) -> None:
        self.parse_parameter_group()
        while self.match('SEMICOLON'):
            if self.current().kind == 'RPAREN':
                break
            self.parse_parameter_group()

    def parse_parameter_group(self) -> None:
        if self.current().kind in {'VAR', 'CONST'}:
            self.pos += 1
        self.parse_identifier_list()
        self.expect('COLON')
        self.parse_type()

    def parse_attribute_section_optional(self) -> None:
        if not self.match('LBRACKET'):
            return
        if self.current().kind != 'RBRACKET':
            self.parse_attribute_item()
            while self.match('COMMA'):
                self.parse_attribute_item()
        self.expect('RBRACKET')

    def parse_attribute_item(self) -> None:
        if self.current().kind == 'ORIGIN':
            self.pos += 1
            self.expect('LPAREN')
            self.parse_constant()
            self.expect('RPAREN')
            return
        if self.current().kind in {'READONLY', 'PUBLIC', 'STATIC', 'EXTERNAL', 'PURE', 'OVERLAY', 'FORTRAN'}:
            self.pos += 1
            return
        self.error('expected attribute item')

    def parse_compound_statement(self) -> None:
        self.expect('BEGIN')
        if self.current().kind != 'END':
            self.parse_statement()
            while self.match('SEMICOLON'):
                if self.current().kind == 'END':
                    break
                self.parse_statement()
        self.expect('END')

    def parse_statement(self) -> None:
        kind = self.current().kind
        if kind == 'BEGIN':
            self.parse_compound_statement()
            return
        if kind == 'IF':
            self.parse_if_statement()
            return
        if kind == 'FOR':
            self.parse_for_statement()
            return
        if kind == 'REPEAT':
            self.parse_repeat_statement()
            return
        if kind == 'WHILE':
            self.parse_while_statement()
            return
        if kind == 'CASE':
            self.parse_case_statement()
            return
        if kind == 'WITH':
            self.parse_with_statement()
            return
        if kind == 'GOTO':
            self.pos += 1
            self.parse_label_id()
            return
        if kind == 'RETURN':
            self.pos += 1
            return
        if kind in {'BREAK', 'CYCLE'}:
            self.pos += 1
            return
        if kind == 'INTEGER_LITERAL' and self.next_kind() == 'COLON':
            self.parse_label_statement()
            return
        if kind == 'IDENTIFIER':
            self.parse_assignment_or_proc_call()
            return
        if kind in {'SEMICOLON', 'END', 'UNTIL', 'ELSE', 'OTHERWISE', 'RPAREN'}:
            return
        self.error('expected statement')

    def parse_assignment_or_proc_call(self) -> None:
        self.expect('IDENTIFIER')
        saw_selector = False
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            saw_selector = True
            self.parse_selector()

        if self.current().kind == 'ASSIGN':
            self.pos += 1
            self.parse_expression()
            return

        if saw_selector:
            self.error('designator statement must be an assignment')

        if self.current().kind == 'LPAREN':
            self.pos += 1
            if self.current().kind != 'RPAREN':
                self.parse_actual_parameter_list()
            self.expect('RPAREN')
        # Bare procedure call is allowed.

    def parse_actual_parameter_list(self) -> None:
        self.parse_actual_parameter()
        while self.match('COMMA'):
            self.parse_actual_parameter()

    def parse_actual_parameter(self) -> None:
        self.parse_expression()
        while self.match('COLON'):
            self.parse_expression()

    def parse_if_statement(self) -> None:
        self.expect('IF')
        self.parse_expression()
        self.expect('THEN')
        self.parse_statement()
        if self.match('ELSE'):
            self.parse_statement()

    def parse_for_statement(self) -> None:
        self.expect('FOR')
        self.expect('IDENTIFIER')
        self.expect('ASSIGN')
        self.parse_expression()
        if self.current().kind in {'TO', 'DOWNTO'}:
            self.pos += 1
        else:
            self.error('expected TO or DOWNTO')
        self.parse_expression()
        self.expect('DO')
        self.parse_statement()

    def parse_repeat_statement(self) -> None:
        self.expect('REPEAT')
        if self.current().kind != 'UNTIL':
            self.parse_statement()
            while self.match('SEMICOLON'):
                if self.current().kind == 'UNTIL':
                    break
                self.parse_statement()
        self.expect('UNTIL')
        self.parse_expression()

    def parse_while_statement(self) -> None:
        self.expect('WHILE')
        self.parse_expression()
        self.expect('DO')
        self.parse_statement()

    def parse_case_statement(self) -> None:
        self.expect('CASE')
        self.parse_expression()
        self.expect('OF')
        if self.current().kind != 'END':
            self.parse_case_element()
            while self.match('SEMICOLON'):
                if self.current().kind in {'OTHERWISE', 'END'}:
                    break
                self.parse_case_element()
        if self.match('OTHERWISE'):
            self.parse_statement()
        self.expect('END')

    def parse_case_element(self) -> None:
        self.parse_case_constant_list()
        self.expect('COLON')
        self.parse_statement()

    def parse_case_constant_list(self) -> None:
        self.parse_case_constant()
        while self.match('COMMA'):
            self.parse_case_constant()

    def parse_case_constant(self) -> None:
        self.parse_constant()
        if self.match('RANGE'):
            self.parse_constant()

    def parse_with_statement(self) -> None:
        self.expect('WITH')
        self.parse_with_target()
        while self.match('COMMA'):
            self.parse_with_target()
        self.expect('DO')
        self.parse_statement()

    def parse_with_target(self) -> None:
        self.expect('IDENTIFIER')
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            self.parse_selector()

    def parse_label_statement(self) -> None:
        self.parse_label_id()
        self.expect('COLON')
        self.parse_statement()

    def parse_selector(self) -> None:
        kind = self.current().kind
        if kind == 'LBRACKET':
            self.pos += 1
            self.parse_expression()
            self.expect('RBRACKET')
            return
        if kind == 'DOT':
            self.pos += 1
            self.expect('IDENTIFIER')
            return
        if kind == 'POINTER':
            self.pos += 1
            return
        self.error('expected selector')

    def parse_expression(self) -> None:
        self.parse_simple_expression()
        if self.current().kind in {'EQ', 'NEQ', 'LT', 'LE', 'GT', 'GE', 'IN'}:
            self.pos += 1
            self.parse_simple_expression()

    def parse_simple_expression(self) -> None:
        if self.current().kind in {'PLUS', 'MINUS', 'NOT'}:
            self.pos += 1
        self.parse_term()
        while self.current().kind in {'PLUS', 'MINUS', 'OR', 'XOR'}:
            self.pos += 1
            self.parse_term()

    def parse_term(self) -> None:
        self.parse_factor()
        while self.current().kind in {'MUL', 'SLASH', 'DIV', 'MOD', 'AND'}:
            self.pos += 1
            self.parse_factor()

    def parse_factor(self) -> None:
        kind = self.current().kind
        if kind == 'IDENTIFIER':
            if self.next_kind() == 'LPAREN':
                self.pos += 1
                self.pos += 1
                if self.current().kind != 'RPAREN':
                    self.parse_actual_parameter_list()
                self.expect('RPAREN')
            else:
                self.parse_designator()
            return
        if kind in {'INTEGER_LITERAL', 'REAL_LITERAL', 'CHAR_LITERAL', 'STRING_LITERAL', 'BOOLEAN_LITERAL'}:
            self.pos += 1
            return
        if kind == 'LPAREN':
            self.pos += 1
            self.parse_expression()
            self.expect('RPAREN')
            return
        if kind == 'ADR':
            self.pos += 1
            self.expect('IDENTIFIER')
            return
        if kind == 'SIZEOF':
            self.pos += 1
            self.expect('LPAREN')
            if self.current().kind == 'IDENTIFIER':
                self.pos += 1
            else:
                self.parse_type()
            self.expect('RPAREN')
            return
        if kind == 'UPPER':
            self.pos += 1
            self.expect('LPAREN')
            self.expect('IDENTIFIER')
            self.expect('RPAREN')
            return
        if kind == 'LBRACKET':
            self.pos += 1
            if self.current().kind != 'RBRACKET':
                self.parse_set_element()
                while self.match('COMMA'):
                    self.parse_set_element()
            self.expect('RBRACKET')
            return
        self.error('expected factor')

    def parse_designator(self) -> None:
        self.expect('IDENTIFIER')
        while self.current().kind in {'LBRACKET', 'DOT', 'POINTER'}:
            self.parse_selector()

    def parse_constant(self) -> None:
        kind = self.current().kind
        if kind in {'INTEGER_LITERAL', 'REAL_LITERAL', 'CHAR_LITERAL', 'STRING_LITERAL', 'BOOLEAN_LITERAL'}:
            self.pos += 1
            return
        if kind == 'IDENTIFIER':
            self.pos += 1
            return
        if kind in {'PLUS', 'MINUS'}:
            self.pos += 1
            self.parse_constant()
            return
        self.error('expected constant')

    def parse_type(self) -> None:
        if self.match('PACKED'):
            pass
        kind = self.current().kind
        if kind in {'ARRAY', 'SUPER'}:
            if kind == 'SUPER':
                self.pos += 1
                self.expect('ARRAY')
            else:
                self.pos += 1
            self.expect('LBRACKET')
            self.parse_index_range()
            self.expect('RBRACKET')
            self.expect('OF')
            self.parse_type()
            return
        if kind == 'RECORD':
            self.pos += 1
            while self.current().kind != 'END':
                self.parse_identifier_list()
                self.expect('COLON')
                self.parse_type()
                if self.current().kind == 'SEMICOLON':
                    self.pos += 1
                else:
                    break
            self.expect('END')
            return
        if kind == 'SET':
            self.pos += 1
            self.expect('OF')
            self.parse_set_base()
            return
        if kind == 'FILE':
            self.pos += 1
            self.expect('OF')
            self.parse_type()
            return
        if kind == 'LSTRING':
            self.pos += 1
            self.expect('LPAREN')
            self.parse_constant()
            self.expect('RPAREN')
            return
        if kind == 'POINTER':
            self.pos += 1
            self.parse_type()
            return
        if kind == 'IDENTIFIER':
            self.pos += 1
            if self.match('LPAREN'):
                self.parse_constant()
                self.expect('RPAREN')
            return
        if kind in {'INTEGER', 'REAL', 'BOOLEAN', 'CHAR', 'WORD', 'ADRMEM'}:
            self.pos += 1
            return
        self.error('expected type')

    def parse_index_range(self) -> None:
        self.parse_constant()
        self.expect('RANGE')
        if self.current().kind == 'MUL':
            self.pos += 1
        else:
            self.parse_constant()

    def parse_set_base(self) -> None:
        if self.current().kind == 'IDENTIFIER':
            if self.next_kind() == 'RANGE':
                self.parse_constant()
                self.expect('RANGE')
                self.parse_constant()
                return
            self.pos += 1
            return
        if self.current().kind in {'INTEGER_LITERAL', 'REAL_LITERAL', 'CHAR_LITERAL', 'STRING_LITERAL', 'BOOLEAN_LITERAL'}:
            self.parse_constant()
            if self.match('RANGE'):
                self.parse_constant()
            return
        self.error('expected set base type or range')

    def parse_set_element(self) -> None:
        self.parse_expression()
        if self.match('RANGE'):
            self.parse_expression()

    def parse_identifier_list(self) -> None:
        self.expect('IDENTIFIER')
        while self.match('COMMA'):
            self.expect('IDENTIFIER')

    def parse_label_id(self) -> None:
        if self.current().kind == 'INTEGER_LITERAL':
            self.pos += 1
            return
        if self.current().kind == 'IDENTIFIER':
            self.pos += 1
            return
        self.error('expected label id')

    def skip_include_directives(self) -> None:
        while self.current().kind == 'INCLUDE_DIRECTIVE':
            self.pos += 1

    def advance_end_semicolon(self) -> None:
        self.expect('END')
        self.expect('SEMICOLON')


def parse_file(path: str) -> None:
    tokens = lex_file(path)
    Parser(tokens).parse()


def main() -> int:
    if len(sys.argv) != 2:
        print('Usage: python3 parser.py <source-file>', file=sys.stderr)
        return 2
    try:
        parse_file(sys.argv[1])
    except (LexerError, ParserError) as exc:
        print(f'Parse error: {exc}', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'File error: {exc}', file=sys.stderr)
        return 1
    print('OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
