import string
import typing

Key = typing.Union[str, tuple[str, ...]]

CAMELSNAKE_TRANS = str.maketrans({c: f'_{c.lower()}' for c in string.ascii_uppercase})

def camel_to_snakecase(camel: str) -> str:
    if camel[0].isupper():
        camel = camel[0].lower() + camel[1:]
    return camel.translate(CAMELSNAKE_TRANS)

def is_dict_subset(sub, sup):
    for key, value in sub.items():
        if key not in sup or sup[key] != value:
            return False
    return True

def is_list_prefix(prefix, full):
    return all(l == r for l, r in zip(prefix, full))

def key_starts_with(key, prefix):
    if isinstance(key, str):
        key = key.split('.')
    if isinstance(prefix, str):
        prefix = prefix.split('.')
    return is_list_prefix(prefix, key)