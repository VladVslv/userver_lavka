#!/usr/bin/env python3
# -*- coding: UTF-8 -*-

import argparse
import collections
import logging
import re

import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ERR_CODES_PAGE = (
    'https://www.postgresql.org/docs/12/static/errcodes-appendix.html'
)
HTML_REGEX = (
    r'(?:class=\"bold\"[\S]*>([^<]+))'
    '|'
    r'(?:class=\"literal\">([^<]+).*?class=.*?\"symbol\">([^<]+))'
)

DEF_OFFSET = '  '
LIT_OFFSET = '  '

CLASSES_ENUM = """
// PostgreSQL error classes
/// Docs: https://www.postgresql.org/docs/12/static/errcodes-appendix.html
/// Enumeration was generated by userver/scripts/postgres/pg_sql_codes.py
enum class SqlStateClass : std::int64_t {"""
ERRORS_ENUM = """
/// PostgreSQL error codes
/// Docs: https://www.postgresql.org/docs/12/static/errcodes-appendix.html
/// Enumeration was generated by userver/scripts/postgres/pg_sql_codes.py
enum class SqlState : std::int64_t {
  kUnknownState, ///!< Unknown state, not in PostgreSQL docs"""
ERROR_LITERALS = """
// This goes to the cpp file in an anonymous namespace
// Data was generated by userver/scripts/postgres/pg_sql_codes.py
const std::unordered_map<std::string_view, SqlState> kCodeStrToState{"""

DISAMBIGUATIONS = {
    'kWarning': 'Warn',
    'kExternalRoutineException': 'Ex',
    'kExternalRoutineInvocationException': 'Ex',
}


def static_vars(**kwargs):
    def decorate(func):
        for k in kwargs:
            setattr(func, k, kwargs[k])
        return func

    return decorate


@static_vars(value=0)
def class_value(prev_count):
    if class_value.value == 0:
        class_value.value = 1
    else:
        val = class_value.value * 2
        while class_value.value + prev_count > val:
            val *= 2
        class_value.value = val
    return class_value.value


def gen_enum_value(snake_str):
    components = snake_str.split('_')
    return 'k' + ''.join(x.title() for x in components)


def print_with_offset(stream, offset, *args):
    lines = '\n'.join(args).split('\n')
    print(offset + ('\n' + offset).join(lines), file=stream)


class ErrorClass:
    error_cnt_by_symbol: dict = collections.Counter()

    class ErrorCode:
        def __init__(self, klass, symbol, literal):
            self.klass = klass
            self.symbol = symbol
            self.literal = literal

        def disambiguated_symbol(self):
            disambiguation = ''
            if ErrorClass.error_cnt_by_symbol[self.symbol] > 1:
                disambiguation = DISAMBIGUATIONS.get(self.klass, '')
            return self.symbol + disambiguation

        def print_decl(self, stream):
            if self.symbol == self.klass:
                print_with_offset(
                    stream,
                    DEF_OFFSET,
                    '{symbol} = static_cast<std::int64_t>('
                    'SqlStateClass::{symbol}), //!< {literal}'.format(
                        symbol=self.disambiguated_symbol(),
                        literal=self.literal,
                    ),
                )
            else:
                print_with_offset(
                    stream,
                    DEF_OFFSET,
                    '{symbol}, //!< {literal}'.format(
                        symbol=self.disambiguated_symbol(),
                        literal=self.literal,
                    ),
                )

        def print_literal(self, stream):
            print_with_offset(
                stream,
                LIT_OFFSET,
                '{{"{literal}", SqlState::{symbol}}},'.format(
                    literal=self.literal, symbol=self.disambiguated_symbol(),
                ),
            )

        def print_class_test(self, stream):
            print_with_offset(
                stream,
                LIT_OFFSET,
                'EXPECT_EQ(pg::SqlStateClass::{klass},'
                ' pg::GetSqlStateClass(pg::SqlState::{symbol}));'.format(
                    klass=self.klass, symbol=self.disambiguated_symbol(),
                ),
            )

        def print_parse_test(self, stream):
            print_with_offset(
                stream,
                LIT_OFFSET,
                'EXPECT_EQ(pg::SqlState::{symbol},'
                ' pg::SqlStateFromString("{literal}"));'.format(
                    symbol=self.disambiguated_symbol(), literal=self.literal,
                ),
            )

    def __init__(self, desc, prev_count):
        self.desc = desc
        self.value = class_value(prev_count)
        self.errors = []
        self.name = None

    def add_error(self, symbol, literal):
        if self.name is None:
            self.name = symbol
        self.errors.append(ErrorClass.ErrorCode(self.name, symbol, literal))
        ErrorClass.error_cnt_by_symbol[symbol] += 1

    def print_class(self, stream):
        print_with_offset(
            stream,
            DEF_OFFSET,
            '{name} = 0x{value:02x},'.format(name=self.name, value=self.value),
        )

    def print_symbols(self, stream):
        print_with_offset(
            stream,
            DEF_OFFSET,
            '//@{',
            '/** @name {desc} */'.format(desc=self.desc),
        )
        for err_class in self.errors:
            err_class.print_decl(stream)
        print_with_offset(stream, DEF_OFFSET, '//@}')

    def print_literals(self, stream):
        print_with_offset(
            stream,
            LIT_OFFSET,
            '//@{',
            '/** @name {desc} */'.format(desc=self.desc),
        )
        for err_class in self.errors:
            err_class.print_literal(stream)
        print_with_offset(stream, LIT_OFFSET, '//@}')

    def print_test(self, stream):
        print_with_offset(
            stream, LIT_OFFSET, '// {desc}'.format(desc=self.desc),
        )
        for err_class in self.errors:
            err_class.print_parse_test(stream)
            err_class.print_class_test(stream)


def main():
    parser = argparse.ArgumentParser(description='SqlCode enum generator')
    parser.add_argument(
        '-o', '--header', help='Outuput header', metavar='out.hpp',
    )
    parser.add_argument(
        '-s', '--source', help='Outuput source', metavar='out.cpp',
    )
    parser.add_argument(
        '-t', '--test', help='Generate test', metavar='test.cpp',
    )

    args = parser.parse_args()

    logger.info('Retrieving documentation')
    page = requests.get(ERR_CODES_PAGE)
    logger.info('Cleaning html')
    page_text = re.sub(
        r'.*?<table[^>]+summary=\"PostgreSQL Error Codes\"[^>]*>',
        '',
        page.text,
        flags=re.MULTILINE | re.DOTALL,
    )
    page_text = re.sub(
        r'</table>.*', '', page_text, flags=re.MULTILINE | re.DOTALL,
    )

    logger.info('Parsing html')
    matches = re.finditer(HTML_REGEX, page_text, re.MULTILINE | re.DOTALL)

    err_count = 0
    current_class = None
    error_classes = []

    logger.info('Collecting definitions')
    for match in matches:
        if match.group(1):
            class_desc = match.group(1).replace('\n', ' ')
            current_class = ErrorClass(class_desc, err_count)
            error_classes.append(current_class)
            err_count = 0
        elif match.group(2):
            literal = match.group(2)
            symbol = gen_enum_value(match.group(3))
            current_class.add_error(symbol, literal)
            err_count += 1

    logger.info('Printing sources')

    if args.header:
        with open(args.header, 'w') as header:
            print(CLASSES_ENUM, file=header)
            for err_class in error_classes:
                err_class.print_class(header)
            print('};\n\n', file=header)

            print(ERRORS_ENUM, file=header)
            for err_class in error_classes:
                err_class.print_symbols(header)
            print('};\n', file=header)

    if args.source:
        with open(args.source, 'w') as source:
            print(ERROR_LITERALS, file=source)
            for err_class in error_classes:
                err_class.print_literals(source)
            print('};\n', file=source)

    if args.test:
        with open(args.test, 'w') as test:
            print(
                '// Test was generated by'
                ' userver/scripts/postgres/pg_sql_codes.py',
                '#include <userver/utest/utest.hpp>',
                '',
                '#include <userver/storages/postgres/exceptions.hpp>',
                '',
                'namespace pg = storages::postgres;',
                '',
                sep='\n',
                file=test,
            )
            for err_class in error_classes:
                print(
                    'TEST(PostgreError, SqlState' + err_class.name[1:] + ') {',
                    file=test,
                )
                err_class.print_test(test)
                print('}\n', file=test)

    logger.info('Done')


if __name__ == '__main__':
    main()