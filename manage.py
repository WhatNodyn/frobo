import frobo
import logging
import os
import pathlib

PROJECT = pathlib.Path(__file__).parent
VENV = pathlib.Path(os.getenv('VIRTUAL_ENV', None))

logging.getLogger('discord_slash').disabled = True
core = frobo.Core([
    # TODO: Determine user module path according to environment
    PROJECT / 'modules',
])

def pad_to(s, l):
    s = str(s)
    return f'{(l - len(s)) * " "}{s}'    

try:
    core.run()
except SystemExit as e:
    raise e
except BaseException as e:
    print(f'\033[1;91m{e.__class__.__name__} raised: {e}')
    current = e.__traceback__
    stack = []
    while current:
        path = pathlib.Path(current.tb_frame.f_code.co_filename)
        if path.is_relative_to(PROJECT):
            path = path.relative_to(PROJECT)
        elif path.is_relative_to(VENV):
            path = '<virtualenv>' / path.relative_to(VENV)
        stack.append((path, current.tb_lineno, current.tb_frame.f_code.co_name, current))
        current = current.tb_next

    max_file_length = len(str(max(stack, key=lambda x: len(str(x[0])))[0]))
    max_line_length = len(str(max(stack, key=lambda x: len(str(x[1])))[1]))
    max_func_length = len(str(max(stack, key=lambda x: len(str(x[2])))[2]))
    for path, line, func_name, trace in stack:
        visual_path = pad_to(f'{path.parent}/\033[96m{path.stem}\033[97m{path.suffix}', max_file_length + 10)
        print(
            f'\033[0;90min file  \033[1;97m{visual_path}',
            f'\033[0;90m:\033[1;93m{pad_to(line, max_line_length)}',
            f'\033[0;90m during function \033[1;95m{pad_to(func_name, max_func_length)}'
            '\033[0m',
            sep='',
        )