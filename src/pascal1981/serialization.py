"""
JSON Serialization and Deserialization for Pascal-1981 tokens, AST, and type system objects.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, List, Sequence, Union

from . import ast_nodes, type_system
from .ast_nodes import ASTNode
from .lexer import Token
from .type_system import Type as TypeSystemType

# ============================================================================
# Token Serialization
# ============================================================================


def token_to_dict(tok: Token) -> dict[str, Any]:
    return {
        'kind': tok.kind,
        'code': tok.code,
        'lexeme': tok.lexeme,
        'value': tok.value,
        'line': tok.line,
        'column': tok.column,
        'flags': tok.flags,
    }


def token_from_dict(data: dict[str, Any]) -> Token:
    return Token(
        kind=data['kind'],
        code=data['code'],
        lexeme=data['lexeme'],
        value=data['value'],
        line=data['line'],
        column=data['column'],
        flags=dict(data['flags']) if data.get('flags') is not None else {},
    )


def tokens_to_json(tokens: Sequence[Token], indent: int | None = None) -> str:
    return json.dumps([token_to_dict(t) for t in tokens], indent=indent)


def tokens_from_json(json_str: str) -> List[Token]:
    data = json.loads(json_str)
    return [token_from_dict(item) for item in data]


# ============================================================================
# AST and Type System Serialization
# ============================================================================


def _to_serializable(obj: Any) -> Any:
    if isinstance(obj, ASTNode):
        res: dict[str, Any] = {'__node_type__': obj.__class__.__name__}
        for k, v in obj.__dict__.items():
            res[k] = _to_serializable(v)
        return res
    elif isinstance(obj, TypeSystemType):
        res: dict[str, Any] = {'__type_system__': obj.__class__.__name__}
        if hasattr(obj, '__dict__'):
            for k, v in obj.__dict__.items():
                res[k] = _to_serializable(v)
        return res
    elif isinstance(obj, list):
        return [_to_serializable(x) for x in obj]
    elif isinstance(obj, tuple):
        return {'__tuple__': True, 'items': [_to_serializable(x) for x in obj]}
    elif isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    else:
        return obj


def _from_serializable(obj: Any) -> Any:
    if isinstance(obj, dict):
        if '__node_type__' in obj:
            node_type = obj['__node_type__']
            cls = getattr(ast_nodes, node_type, None)
            if cls is None:
                raise ValueError(f"Unknown AST node type: {node_type}")
            kwargs = {}
            for k, v in obj.items():
                if k == '__node_type__':
                    continue
                kwargs[k] = _from_serializable(v)

            if dataclasses.is_dataclass(cls):
                field_names = {f.name for f in dataclasses.fields(cls)}
                init_kwargs = {k: v for k, v in kwargs.items() if k in field_names}
                extra_attrs = {k: v for k, v in kwargs.items() if k not in field_names}
                instance = cls(**init_kwargs)
                for k, v in extra_attrs.items():
                    setattr(instance, k, v)
                return instance
            else:
                instance = cls(**kwargs)
                return instance

        elif '__type_system__' in obj:
            type_name = obj['__type_system__']
            cls = getattr(type_system, type_name, None)
            if cls is None:
                raise ValueError(f"Unknown TypeSystem type: {type_name}")
            kwargs = {}
            for k, v in obj.items():
                if k == '__type_system__':
                    continue
                kwargs[k] = _from_serializable(v)

            if dataclasses.is_dataclass(cls):
                field_names = {f.name for f in dataclasses.fields(cls)}
                init_kwargs = {k: v for k, v in kwargs.items() if k in field_names}
                extra_attrs = {k: v for k, v in kwargs.items() if k not in field_names}
                instance = cls(**init_kwargs)
                for k, v in extra_attrs.items():
                    setattr(instance, k, v)
                return instance
            else:
                try:
                    instance = cls(**kwargs)
                except TypeError:
                    instance = cls()
                    for k, v in kwargs.items():
                        setattr(instance, k, v)
                return instance

        elif obj.get('__tuple__') is True:
            return tuple(_from_serializable(x) for x in obj.get('items', []))
        else:
            return {k: _from_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_from_serializable(x) for x in obj]
    else:
        return obj


def ast_to_dict(ast: ASTNode) -> dict[str, Any]:
    return _to_serializable(ast)


def ast_from_dict(data: dict[str, Any]) -> ASTNode:
    res = _from_serializable(data)
    if not isinstance(res, ASTNode):
        raise TypeError(f"Deserialized object is not an ASTNode: {type(res)}")
    return res


def ast_to_json(ast: ASTNode, indent: int | None = None) -> str:
    return json.dumps(ast_to_dict(ast), indent=indent)


def ast_from_json(json_str: str) -> ASTNode:
    data = json.loads(json_str)
    return ast_from_dict(data)
