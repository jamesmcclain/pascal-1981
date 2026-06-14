from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Set

# Token code assignments. These are stable for hex-stream output.
KEYWORD_CODES = {
    'PROGRAM': 0x0001,
    'MODULE': 0x0002,
    'INTERFACE': 0x0003,
    'IMPLEMENTATION': 0x0004,
    'UNIT': 0x0058,
    'USES': 0x0005,
    'CONST': 0x0006,
    'CONSTS': 0x005A,
    'TYPE': 0x0007,
    'VAR': 0x0008,
    'VARS': 0x0059,
    'VALUE': 0x0009,
    'LABEL': 0x000A,
    'PROCEDURE': 0x000B,
    'FUNCTION': 0x000C,
    'BEGIN': 0x000D,
    'END': 0x000E,
    'IF': 0x000F,
    'THEN': 0x0010,
    'ELSE': 0x0011,
    'FOR': 0x0012,
    'TO': 0x0013,
    'DOWNTO': 0x0014,
    'DO': 0x0015,
    'REPEAT': 0x0016,
    'UNTIL': 0x0017,
    'WHILE': 0x0018,
    'CASE': 0x0019,
    'OF': 0x001A,
    'OTHERWISE': 0x001B,
    'WITH': 0x001C,
    'GOTO': 0x001D,
    'BREAK': 0x001E,
    'CYCLE': 0x001F,
    'RETURN': 0x0020,
    'EXTERN': 0x0021,
    'EXTERNAL': 0x0022,
    'FORWARD': 0x0023,
    'PACKED': 0x0024,
    'SUPER': 0x0025,
    'ARRAY': 0x0026,
    'RECORD': 0x0027,
    'SET': 0x0028,
    'FILE': 0x0029,
    'LSTRING': 0x002A,
    'ORIGIN': 0x002B,
    'READONLY': 0x002C,
    'PUBLIC': 0x002D,
    'STATIC': 0x002E,
    'PURE': 0x002F,
    'OVERLAY': 0x0030,
    'FORTRAN': 0x0031,
    'ADR': 0x0032,
    'ADS': 0x005C,
    'SIZEOF': 0x0033,
    'UPPER': 0x0034,
    'LOWER': 0x005D,
    'IN': 0x0035,
    'DIV': 0x0036,
    'MOD': 0x0037,
    'OR': 0x0038,
    'XOR': 0x0039,
    'AND': 0x003A,
    'NOT': 0x0057,
    'NIL': 0x005B,
}

SYMBOL_CODES = {
    'ASSIGN': 0x003B,  # :=
    'EQ': 0x003C,  # =
    'NEQ': 0x003D,  # <>
    'LT': 0x003E,  # <
    'LE': 0x003F,  # <=
    'GT': 0x0040,  # >
    'GE': 0x0041,  # >=
    'PLUS': 0x0042,  # +
    'MINUS': 0x0043,  # -
    'MUL': 0x0044,  # *
    'SLASH': 0x0045,  # /
    'RANGE': 0x0046,  # ..
    'POINTER': 0x0047,  # ^
    'LBRACKET': 0x0048,  # [
    'RBRACKET': 0x0049,  # ]
    'LPAREN': 0x004A,  # (
    'RPAREN': 0x004B,  # )
    'SEMICOLON': 0x004C,  # ;
    'COMMA': 0x004D,  # ,
    'COLON': 0x004E,  # :
    'DOT': 0x004F,  # .
}

LITERAL_CODES = {
    'IDENTIFIER': 0x0050,
    'INTEGER_LITERAL': 0x0051,
    'REAL_LITERAL': 0x0052,
    'CHAR_LITERAL': 0x0053,
    'STRING_LITERAL': 0x0054,
    'BOOLEAN_LITERAL': 0x0055,
    'INCLUDE_DIRECTIVE': 0x0056,
    'EOF': 0x0000,
}

ALL_CODES = {}
ALL_CODES.update(KEYWORD_CODES)
ALL_CODES.update(SYMBOL_CODES)
ALL_CODES.update(LITERAL_CODES)


@dataclass(frozen=True)
class Token:
    kind: str
    code: int
    lexeme: str
    value: Any
    line: int
    column: int
    flags: dict[str, bool]


class LexerError(Exception):
    pass


# ---------------------------------------------------------------------------
# Metacommand tables  (IBM Pascal manual, Chapter 4)
# ---------------------------------------------------------------------------

# ON/OFF switches that are stamped onto every emitted token so the parser and
# codegen can read the in-effect state at any source location.  Defaults are
# per the manual (§4-10 … §4-35).
_ON_OFF_FLAGS: dict[str, bool] = {
    'BRAVE': True,  # errors/warnings to display         (default +)
    'DEBUG': True,  # master runtime debug switch        (default +)
    'ENTRY': False,  # proc entry/exit for debugger       (default -)
    'GOTO': False,  # flag GOTO statements               (default -)
    'INDEXCK': True,  # array index range check            (default +)
    'INITCK': False,  # initialise uninitialised variables (default -)
    'LINE': False,  # line-number calls for debugger     (default -)
    'LIST': True,  # source listing                     (default +)
    'MATHCK': True,  # integer overflow / div-by-zero     (default +)
    'NILCK': True,  # pointer dereference check          (default +)
    'OCODE': True,  # object-code listing                (default +)
    'RANGECK': True,  # subrange validity                  (default +)
    'RUNTIME': False,  # runtime error location mode        (default -)
    'STACKCK': True,  # stack overflow check               (default +)
    'SYMTAB': True,  # symbol-table listing               (default +)
    'WARN': True,  # warnings                           (default +)
}

# $DEBUG master switch controls these sub-flags (manual §4-11).
_DEBUG_SUB_FLAGS: frozenset[str] = frozenset({'ENTRY', 'INDEXCK', 'INITCK', 'MATHCK', 'NILCK', 'RANGECK', 'STACKCK'})

# INTEGER metacommands (listing/output — no semantic effect on codegen).
# Defaults from the manual where stated.
_INT_META_DEFAULTS: dict[str, int] = {
    'ERRORS': 25,
    'LINESIZE': 79,
    'PAGE': 1,
    'PAGEIF': 0,
    'PAGESIZE': 53,
    'SKIP': 0,
}

# STRING metacommands (listing/output — no semantic effect on codegen).
_STR_META_DEFAULTS: dict[str, str] = {
    'SUBTITLE': '',
    'TITLE': '',
}


class Lexer:

    def __init__(self, source: str):
        self.source = source
        self.pos = 0
        self.line = 1
        self.column = 1
        self.length = len(source)
        # ON/OFF flags stamped onto every token (parser/codegen read these).
        self.meta_flags: dict[str, bool] = dict(_ON_OFF_FLAGS)
        # Integer and string metacommand values (used only at compile time).
        self._meta_int: dict[str, int] = dict(_INT_META_DEFAULTS)
        self._meta_str: dict[str, str] = dict(_STR_META_DEFAULTS)
        # PUSH/POP stack: each entry is a snapshot of (meta_flags, _meta_int, _meta_str).
        self._flag_stack: list[tuple[dict, dict, dict]] = []
        # $INCONST identifiers (integer meta-constants usable in $IF conditions).
        self._meta_consts: dict[str, int] = {}

    def current(self) -> str:
        return self.source[self.pos] if self.pos < self.length else ''

    def peek(self, offset: int = 1) -> str:
        index = self.pos + offset
        return self.source[index] if index < self.length else ''

    def startswith(self, text: str) -> bool:
        return self.source.startswith(text, self.pos)

    def advance(self, count: int = 1) -> None:
        for _ in range(count):
            if self.pos >= self.length:
                return
            ch = self.source[self.pos]
            self.pos += 1
            if ch == '\n':
                self.line += 1
                self.column = 1
            else:
                self.column += 1

    def emit(self, kind: str, lexeme: str, value: Any, line: int, column: int) -> Token:
        return Token(kind=kind, code=ALL_CODES[kind], lexeme=lexeme, value=value, line=line, column=column, flags=dict(self.meta_flags))

    def skip_whitespace(self) -> None:
        while self.current() and self.current() in ' \t\r\n':
            self.advance()

    def skip_comment(self) -> bool:
        if self.startswith('(*'):
            self.advance(2)
            if self.current() == '$':
                self.parse_metacommand_comment('*)')
                return True
            while self.current() and not self.startswith('*)'):
                self.advance()
            if not self.current():
                raise LexerError(f"Unterminated comment at line {self.line}, column {self.column}")
            self.advance(2)
            return True

        if self.current() == '{':
            self.advance()
            if self.current() == '$':
                self.parse_metacommand_comment('}')
                return True
            while self.current() and self.current() != '}':
                self.advance()
            if not self.current():
                raise LexerError(f"Unterminated comment at line {self.line}, column {self.column}")
            self.advance()
            return True

        return False

    def try_include_directive(self) -> Optional[Token]:
        if self.startswith('(*$INCLUDE:') or self.startswith('{$INCLUDE:'):
            line, column = self.line, self.column
            opener = '(*$INCLUDE:' if self.startswith('(*$INCLUDE:') else '{$INCLUDE:'
            closer = '*)' if opener.startswith('(*') else '}'
            self.advance(len(opener))
            self.skip_whitespace()
            if self.current() != "'":
                raise LexerError(f"Malformed include directive at line {self.line}, column {self.column}")
            filename = self.read_quoted_filename()
            self.skip_whitespace()
            if not self.startswith(closer):
                raise LexerError(f"Malformed include directive at line {self.line}, column {self.column}")
            self.advance(len(closer))
            return self.emit('INCLUDE_DIRECTIVE', filename, filename, line, column)
        return None

    # ------------------------------------------------------------------
    # Metacommand helpers
    # ------------------------------------------------------------------

    def _read_meta_name(self) -> str:
        """Read an identifier from current position (upper-cased)."""
        start = self.pos
        while self.current() and (self.current().isalpha() or self.current().isdigit() or self.current() == '_'):
            self.advance()
        return self.source[start:self.pos].upper()

    def _consume_to(self, closer: str) -> None:
        """Advance past the next occurrence of `closer`."""
        while self.current() and not self.startswith(closer):
            self.advance()
        if not self.current():
            raise LexerError(f"Unterminated comment at line {self.line}, column {self.column}")
        self.advance(len(closer))

    def _eval_meta_const(self, token: str) -> int:
        """Evaluate a metacommand constant: integer literal or $INCONST name.
        Identifiers that match an ON/OFF flag return 1 if the flag is on.
        Returns 0 for unknown identifiers (treats as false).
        """
        if not token:
            return 0
        try:
            return int(token)
        except ValueError:
            upper = token.upper()
            if upper in self._meta_consts:
                return self._meta_consts[upper]
            if upper in self.meta_flags:
                return 1 if self.meta_flags[upper] else 0
            return 0

    def _skip_source_block(self, current_closer: str, stop_at_else: bool) -> str:
        """Skip a conditional source block, tracking $IF nesting.

        Closes the current metacommand comment (advances past `current_closer`),
        then scans forward until a matching $ELSE (if stop_at_else) or $END at
        depth 1 is found.

        Returns 'ELSE' or 'END'.
        Nested $IF/$END pairs increment/decrement depth so inner blocks are
        skipped atomically.

        Vintage duplicate-$ELSE behavior (D-003): even when stop_at_else is
        False because a completed true-branch is being skipped, a second
        depth-1 $ELSE terminates the skip and tokenization resumes after it.
        This leaks the later branch text, matching the observed 1981 output.

        String literals ('...') in the skipped text are honored: a comment
        opener inside a quoted string does not start a comment, so e.g.
        x := '{' inside a skipped block cannot derail $IF/$END tracking.
        """
        self._consume_to(current_closer)  # close the comment we're in
        depth = 1
        while self.current():
            if self.current() == "'":
                # Skip a string literal verbatim; doubled '' quotes inside a
                # string are naturally handled as two adjacent literals.
                self.advance()
                while self.current() and self.current() != "'":
                    self.advance()
                if self.current() == "'":
                    self.advance()
            elif self.startswith('(*') or self.current() == '{':
                is_paren = self.startswith('(*')
                inner_closer = '*)' if is_paren else '}'
                self.advance(2 if is_paren else 1)
                if self.current() == '$':
                    self.advance()  # consume '$'
                    self.skip_whitespace()
                    tag = self._read_meta_name()
                    if tag == 'IF':
                        # Nested $IF: scan forward in this comment for $THEN,
                        # then increment depth.
                        while self.current() and not self.startswith(inner_closer):
                            if self.current().isalpha():
                                word_start = self.pos
                                while self.current().isalpha():
                                    self.advance()
                                if self.source[word_start:self.pos].upper() == 'THEN':
                                    break
                            else:
                                self.advance()
                        depth += 1
                        self._consume_to(inner_closer)
                    elif tag == 'END' and depth == 1:
                        self._consume_to(inner_closer)
                        return 'END'
                    elif tag == 'ELSE' and depth == 1:
                        self._consume_to(inner_closer)
                        return 'ELSE'
                    elif tag == 'END':
                        depth -= 1
                        self._consume_to(inner_closer)
                    else:
                        self._consume_to(inner_closer)
                else:
                    self._consume_to(inner_closer)
            else:
                self.advance()
        raise LexerError("Unterminated $IF: missing $END")

    # ------------------------------------------------------------------
    # Main metacommand dispatcher
    # ------------------------------------------------------------------

    def parse_metacommand_comment(self, closer: str) -> None:
        """Parse and act on one or more comma-separated metacommands.

        Called after the comment-opener has been consumed and '$' is the
        current character.  Advances past `closer` before returning.

        Tiers (IBM Pascal manual, Chapter 4):
          Tier 1 – listing/output: accepted and silently ignored.
          Tier 2 – ON/OFF runtime checks: stored in self.meta_flags.
          Tier 3 – conditional compilation ($IF/$PUSH/$POP/$MESSAGE/$INCONST).
        """
        self.advance()  # consume '$'

        while True:
            self.skip_whitespace()
            if not self.current() or self.startswith(closer):
                break
            if not (self.current().isalpha() or self.current() == '_'):
                break

            name = self._read_meta_name()
            self.skip_whitespace()

            # ── Tier 3: conditional compilation ────────────────────────

            if name == 'IF':
                # Syntax: $IF constant $THEN
                # Read the constant token (integer literal or identifier).
                const_token = ''
                if self.current().isdigit():
                    start = self.pos
                    while self.current().isdigit():
                        self.advance()
                    const_token = self.source[start:self.pos]
                elif self.current() == '-' and self.peek().isdigit():
                    start = self.pos
                    self.advance()  # consume '-'
                    while self.current().isdigit():
                        self.advance()
                    const_token = self.source[start:self.pos]
                elif self.current().isalpha() or self.current() == '_':
                    const_token = self._read_meta_name()
                self.skip_whitespace()
                # Expect $THEN within the same comment.
                if self.current() == '$':
                    self.advance()
                    self.skip_whitespace()
                    kw = self._read_meta_name()
                    if kw != 'THEN':
                        # Malformed; absorb the rest of the comment silently.
                        self._consume_to(closer)
                        return
                # Evaluate condition.
                cond = self._eval_meta_const(const_token)
                if cond <= 0:
                    # False branch: skip source to $ELSE or $END.
                    result = self._skip_source_block(closer, stop_at_else=True)
                    if result == 'END':
                        return  # no else-branch; done
                    # result == 'ELSE': tokenize continues normally into else-branch;
                    # the eventual {$END} comment will be a no-op.
                else:
                    # True branch: close comment and keep tokenizing.
                    # When we hit {$ELSE}, we skip to $END.
                    # When we hit {$END}, it's a no-op.
                    self._consume_to(closer)
                return

            if name == 'ELSE':
                # Reached during the true branch of an $IF.
                # Skip source forward to the matching $END.
                self._skip_source_block(closer, stop_at_else=False)
                return

            if name == 'END':
                # End-marker for a completed $IF block.  No-op.
                self._consume_to(closer)
                return

            if name == 'PUSH':
                self._flag_stack.append((
                    dict(self.meta_flags),
                    dict(self._meta_int),
                    dict(self._meta_str),
                ))
                # PUSH is typeless; no argument to consume.

            elif name == 'POP':
                if self._flag_stack:
                    saved_flags, saved_int, saved_str = self._flag_stack.pop()
                    self.meta_flags.update(saved_flags)
                    self._meta_int.update(saved_int)
                    self._meta_str.update(saved_str)
                # Silently ignore POP on an empty stack (matches lenient original).

            elif name == 'MESSAGE':
                # $MESSAGE: 'text'  — print to stderr during compilation.
                if self.current() == ':':
                    self.advance()
                    self.skip_whitespace()
                if self.current() == "'":
                    self.advance()
                    msg_start = self.pos
                    while self.current() and self.current() != "'":
                        self.advance()
                    msg = self.source[msg_start:self.pos]
                    if self.current() == "'":
                        self.advance()
                    print(f"[Pascal] {msg}", file=sys.stderr)

            elif name == 'INCONST':
                # $INCONST: identifier  — prompt for a WORD constant.
                # Non-interactive build: print a notice and use 0.
                if self.current() == ':':
                    self.advance()
                    self.skip_whitespace()
                ident = ''
                if self.current().isalpha() or self.current() == '_':
                    ident = self._read_meta_name()
                if ident:
                    print(f"[Pascal] $INCONST: '{ident}' — non-interactive build, using 0", file=sys.stderr)
                    self._meta_consts[ident] = 0

            # ── Tier 1 / Tier 2: flag and listing metacommands ──────────

            elif name in _ON_OFF_FLAGS:
                # ON/OFF switch: +, -, :n, or bare (treat bare as +).
                value: bool
                if self.current() == '+':
                    self.advance()
                    value = True
                elif self.current() == '-':
                    self.advance()
                    value = False
                elif self.current() == ':':
                    self.advance()
                    self.skip_whitespace()
                    num_start = self.pos
                    if self.current() in '+-':
                        self.advance()
                    while self.current().isdigit():
                        self.advance()
                    try:
                        value = int(self.source[num_start:self.pos]) > 0
                    except ValueError:
                        value = True
                else:
                    value = True  # bare name = enable

                self.meta_flags[name] = value

                # $DEBUG couples to its sub-flags (manual §4-11).
                if name == 'DEBUG':
                    for sub in _DEBUG_SUB_FLAGS:
                        self.meta_flags[sub] = value

                # $LINE+ implies $ENTRY+ (manual §4-20).
                if name == 'LINE' and value:
                    self.meta_flags['ENTRY'] = True

            elif name in _INT_META_DEFAULTS:
                # Integer metacommand: consume optional :n.
                if self.current() == ':':
                    self.advance()
                    self.skip_whitespace()
                    num_start = self.pos
                    if self.current() in '+-':
                        self.advance()
                    while self.current().isdigit():
                        self.advance()
                    try:
                        self._meta_int[name] = int(self.source[num_start:self.pos])
                    except ValueError:
                        pass
                # Bare $PAGE (no colon) is typeless (skip to next page).

            elif name in _STR_META_DEFAULTS:
                # String metacommand: consume :'text' or :identifier.
                if self.current() == ':':
                    self.advance()
                    self.skip_whitespace()
                    if self.current() == "'":
                        self.advance()
                        s_start = self.pos
                        while self.current() and self.current() != "'":
                            self.advance()
                        self._meta_str[name] = self.source[s_start:self.pos]
                        if self.current() == "'":
                            self.advance()
                    elif self.current().isalpha() or self.current() == '_':
                        self._meta_str[name] = self._read_meta_name()

            # Any other name (INCLUDE handled separately, unknown names) is
            # silently absorbed.  Consume an optional +/- or :arg so we don't
            # misparse the rest of the comment.
            else:
                if self.current() in '+-':
                    self.advance()
                elif self.current() == ':':
                    self.advance()
                    self.skip_whitespace()
                    if self.current() == "'":
                        self.advance()
                        while self.current() and self.current() != "'":
                            self.advance()
                        if self.current() == "'":
                            self.advance()
                    else:
                        while self.current() and self.current() not in ' \t,}' and not self.startswith('*)'):
                            self.advance()

            # Allow comma-separated metacommands in one comment.
            self.skip_whitespace()
            if self.current() == ',':
                self.advance()
                # Expect next metacommand to start with '$'.
                self.skip_whitespace()
                if self.current() == '$':
                    self.advance()
                continue
            break

        # Close the comment.
        self._consume_to(closer)

    def read_quoted_filename(self) -> str:
        if self.current() != "'":
            raise LexerError(f"Expected quoted filename at line {self.line}, column {self.column}")
        self.advance()  # opening quote
        chars: List[str] = []
        while self.current() and self.current() != "'":
            chars.append(self.current())
            self.advance()
        if not self.current():
            raise LexerError(f"Unterminated filename at line {self.line}, column {self.column}")
        self.advance()  # closing quote
        return ''.join(chars)

    def read_identifier_or_keyword(self) -> Token:
        line, column = self.line, self.column
        start = self.pos
        while self.current() and (self.current().isalnum() or self.current() == '_'):
            self.advance()
        lexeme = self.source[start:self.pos]
        upper = lexeme.upper()

        if upper == 'TRUE':
            return self.emit('BOOLEAN_LITERAL', lexeme, True, line, column)
        if upper == 'FALSE':
            return self.emit('BOOLEAN_LITERAL', lexeme, False, line, column)
        if upper in KEYWORD_CODES:
            return self.emit(upper, lexeme, upper, line, column)
        return self.emit('IDENTIFIER', lexeme, lexeme, line, column)

    def read_number(self) -> Token:
        line, column = self.line, self.column
        start = self.pos
        while self.current() and self.current().isdigit():
            self.advance()

        if self.current() == '#':
            base_text = self.source[start:self.pos]
            base = int(base_text)
            if base < 2 or base > 16:
                raise LexerError(f"Invalid radix {base} at line {line}, column {column}")
            self.advance()  # consume '#'
            digits_start = self.pos
            digits = []
            valid = '0123456789ABCDEF'[:base]
            while self.current() and self.current().upper() in valid:
                digits.append(self.current())
                self.advance()
            if not digits:
                raise LexerError(f"Invalid radix constant at line {line}, column {column}")
            lexeme = self.source[start:self.pos]
            return self.emit('INTEGER_LITERAL', lexeme, int(''.join(digits), base), line, column)

        # Real number only if we see digit+ '.' digit
        if self.current() == '.' and self.peek() != '.' and self.peek().isdigit():
            self.advance()  # dot
            while self.current() and self.current().isdigit():
                self.advance()
            self._read_exponent()
            lexeme = self.source[start:self.pos]
            return self.emit('REAL_LITERAL', lexeme, float(lexeme), line, column)

        lexeme = self.source[start:self.pos]
        return self.emit('INTEGER_LITERAL', lexeme, int(lexeme), line, column)

    def _read_exponent(self) -> None:
        if self.current().upper() != 'E':
            return
        i = 1
        if self.peek(i) in '+-':
            i += 1
        if not self.peek(i).isdigit():  # no digits -> not an exponent; leave 'E'
            return
        self.advance()  # consume 'E'
        if self.current() in '+-':
            self.advance()
        while self.current().isdigit():
            self.advance()

    def read_string_or_char(self) -> Token:
        line, column = self.line, self.column
        self.advance()  # opening quote
        chars: List[str] = []
        while True:
            ch = self.current()
            if not ch:
                raise LexerError(f"Unterminated string literal at line {line}, column {column}")
            if ch == "'":
                if self.peek() == "'":
                    chars.append("'")
                    self.advance(2)
                    continue
                self.advance()  # closing quote
                break
            chars.append(ch)
            self.advance()

        value = ''.join(chars)
        lexeme = "'" + value.replace("'", "''") + "'"
        if len(value) == 1:
            return self.emit('CHAR_LITERAL', lexeme, value, line, column)
        return self.emit('STRING_LITERAL', lexeme, value, line, column)

    def read_operator_or_punct(self) -> Token:
        line, column = self.line, self.column

        two_char = self.source[self.pos:self.pos + 2]
        if two_char == ':=':
            self.advance(2)
            return self.emit('ASSIGN', ':=', ':=', line, column)
        if two_char == '<>':
            self.advance(2)
            return self.emit('NEQ', '<>', '<>', line, column)
        if two_char == '<=':
            self.advance(2)
            return self.emit('LE', '<=', '<=', line, column)
        if two_char == '>=':
            self.advance(2)
            return self.emit('GE', '>=', '>=', line, column)
        if two_char == '..':
            self.advance(2)
            return self.emit('RANGE', '..', '..', line, column)

        ch = self.current()
        single_map = {
            '=': 'EQ',
            '<': 'LT',
            '>': 'GT',
            '+': 'PLUS',
            '-': 'MINUS',
            '*': 'MUL',
            '/': 'SLASH',
            '^': 'POINTER',
            '[': 'LBRACKET',
            ']': 'RBRACKET',
            '(': 'LPAREN',
            ')': 'RPAREN',
            ';': 'SEMICOLON',
            ',': 'COMMA',
            ':': 'COLON',
            '.': 'DOT',
        }
        if ch in single_map:
            kind = single_map[ch]
            self.advance()
            return self.emit(kind, ch, ch, line, column)

        raise LexerError(f"Unexpected character {ch!r} at line {line}, column {column}")

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []

        while self.current():
            self.skip_whitespace()
            if not self.current() or self.current() == '\x1a':
                break

            include = self.try_include_directive()
            if include is not None:
                tokens.append(include)
                continue

            if self.skip_comment():
                continue

            ch = self.current()
            if ch == '\x1a':
                break
            if ch.isalpha() or ch == '_':
                tokens.append(self.read_identifier_or_keyword())
            elif ch.isdigit():
                tokens.append(self.read_number())
            elif ch == "'":
                tokens.append(self.read_string_or_char())
            else:
                tokens.append(self.read_operator_or_punct())

        tokens.append(self.emit('EOF', '', None, self.line, self.column))
        return tokens


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return repr(value)
    return str(value)


def tokens_to_hex(tokens: List[Token], include_eof: bool = False, annotate_values: bool = True) -> str:
    stream = tokens if include_eof else [tok for tok in tokens if tok.kind != 'EOF']
    value_kinds = {
        'IDENTIFIER',
        'INTEGER_LITERAL',
        'REAL_LITERAL',
        'CHAR_LITERAL',
        'STRING_LITERAL',
        'BOOLEAN_LITERAL',
        'INCLUDE_DIRECTIVE',
    }
    parts = []
    for tok in stream:
        text = f'0x{tok.code:04X}'
        if annotate_values and tok.kind in value_kinds:
            text += f'{{{_format_value(tok.value)}}}'
        parts.append(text)
    return ' '.join(parts)


def lex_file(path: str, _include_stack: Optional[Set[Path]] = None) -> List[Token]:
    """Lex a Pascal source file, splicing $INCLUDE files inline.

    MS-Pascal's $INCLUDE argument is a literal filename.  We resolve it
    relative to the including file and do no extension inference.
    """
    source_path = Path(path).resolve()
    if _include_stack is None:
        _include_stack = set()
    if source_path in _include_stack:
        raise LexerError(f"Recursive include detected for {source_path}")

    _include_stack.add(source_path)
    try:
        with open(source_path, 'r', encoding='utf-8', errors='replace') as f:
            raw_tokens = Lexer(f.read()).tokenize()

        tokens: List[Token] = []
        for tok in raw_tokens:
            if tok.kind == 'INCLUDE_DIRECTIVE':
                include_path = source_path.parent / tok.value
                if not include_path.exists():
                    lowered = tok.value.lower()
                    for child in source_path.parent.iterdir():
                        if child.name.lower() == lowered:
                            include_path = child
                            break
                include_path = include_path.resolve()
                included = lex_file(str(include_path), _include_stack)
                tokens.extend(t for t in included if t.kind != 'EOF')
            else:
                tokens.append(tok)
        return tokens
    finally:
        _include_stack.remove(source_path)


def main() -> int:
    if len(sys.argv) != 2:
        print('Usage: python3 lexer.py <source-file>', file=sys.stderr)
        return 2

    try:
        tokens = lex_file(sys.argv[1])
    except LexerError as exc:
        print(f'Lexer error: {exc}', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'File error: {exc}', file=sys.stderr)
        return 1

    print(tokens_to_hex(tokens))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
