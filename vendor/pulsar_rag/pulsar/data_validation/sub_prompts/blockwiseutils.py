import re
from functools import lru_cache
import pandas as pd
import xmlschema
from defusedxml.minidom import parseString
from general.loggerutils import get_logger
import defusedxml.ElementTree as ET
from pulsar.data_validation.sub_prompts.subprompt_formatter import RuleHandler

logger = get_logger("GRAVTY_AI", "INFO")
block_type_pattern = r'type="([^"]+)"'

operations = ["+", "*", "/", "-", "%", "==", "!=", "null", "not_null",
              "in", "not_in", "contains", "not_contains", "starts_with",
              ">", ">=", "<", "<=", "between"]


def nesting_check(xml):
    block_stack = []
    lines = xml.split("\n")

    for line in lines:
        line = line.strip()
        if line in ["<next>", "</next>"]:
            continue
        if line:
            if line.startswith("<") and not re.search(r"(</\w+>|/>)", line):
                if block_stack:
                    last_block = block_stack[-1]
                    str_block = last_block.split()
                    str_line = line.split()
                    if check_validity(str_line, str_block):
                        return True
                    else:
                        block_stack.append(line)
                else:
                    block_stack.append(line)
            elif block_stack and re.search(r"(</\w+>|/>)", line) and not line.startswith("<field"):
                block_stack.pop()

    return block_stack


def check_validity(line, block):
    if len(line) < 2 or len(block) < 2:
        return False
    if line[1] in ["type=\"rule_line\"", "type=\"rule\"", "type=\"payment_rule\""]:
        if block[1] == 'name="ACTION">':
            return True
    return False


def format_xml(xml_string: str):
    """Format the given XML string with indentation."""
    try:
        dom = parseString(xml_string)
        pretty_xml_as_string = dom.toprettyxml(indent="  ")
        return pretty_xml_as_string
    except Exception as e:
        logger.error(f"Error formatting XML: {e}")
        return xml_string.replace('<?xml version="1.0" ?>', '')


def arithmetic_operations_handle(lst):
    i = 0
    for words in lst:
        if is_op(words) or (len(words) > 1 and is_op(words[:1])):
            break
        i += 1
    sentence = lst[0:i] + list(algo_arithmetic_operations(lst[i:]))

    return sentence


def algo_arithmetic_operations(prefix):
    stack = []

    for i in range(len(prefix) - 1, -1, -1):
        if is_op(prefix[i]) and stack:
            if len(stack) >= 2:
                a, b = stack.pop(), stack.pop()
                if 'DAYS' in a or 'MONTHS' in a or 'YEARS' in a:
                    stack += [f"( {b} {prefix[i]} {a} )"]
                else:
                    stack += [f"( {a} {prefix[i]} {b} )"]
            else:
                b = stack.pop()
                stack += [f"{prefix[i]} {b}"]

        else:
            stack += [prefix[i]]
    return stack


def is_op(var):
    return var in operations


def in_operator_fix(df: pd.DataFrame):
    prompt: str = df['Lines']
    xml_str = df['Blocks']
    value = re.search(r'value="(.*?)".*(<field name="operators">in</field>)</block>', xml_str)
    if not value:
        value = re.search(r'value="(.*?)".*(<field name="operators">not_in</field>)</block>', xml_str)
    if value:
        new_prompt = re.sub(r"\{xyz.*", value.groups()[0], prompt)
        new_xml = xml_str.replace(value.groups()[1],
                                  value.groups()[1] + f"<field name=\"value\">{value.groups()[0]}</field>")
        return pd.Series({'Lines': new_prompt, 'Blocks': new_xml})
    return pd.Series({'Lines': prompt.replace("{xyz}", ""), 'Blocks': xml_str})


async def sentence_generator(rule: list, output_subPrompts: list, index: int, block_list: list, attribute_names: dict):
    if 'if_payment_attribute' == rule[1].split(":")[0]:
        rule[1] = 'if_payment' + " " + rule[1].split(":")[1]
    elif 'if' in rule[1].split(":")[0]:
        rule[1] = rule[1].split("_", 1)[0] + " " + rule[1].split(":")[1]
    elif 'activity_criteria' == rule[1].split(":")[0]:
        rule[1] = 'offer_criteria' + " " + rule[1].split(":")[1]
    if ':' in rule[1]:
        rule[1] = rule[1].split(":")[1]
    idx = 1
    while idx < len(rule):
        RuleHandler(rule=rule, rule_index=idx, blocks=block_list, attribute_names=attribute_names, block_index=index)
        idx += 1
    rule_string = " ".join(arithmetic_operations_handle(rule))
    output_subPrompts[index] = rule_string


async def validation(block_str: str, rule: list):
    if validate_block(block_str):
        match = re.search(block_type_pattern, block_str)
        if match:
            block_type = match.group(1)
            rule_type = rule[1].split(':')[0] if isinstance(rule[1], str) else None

            if rule_type and block_type != rule_type:
                raise ValueError("XML validation failed.")
        else:
            logger.warning("Block type pattern not found in block string")
    else:
        raise ValueError("XML validation failed.")


@lru_cache(maxsize=1)
def _block_schema():
    """Parsed and compiled once per process: building it is the slow part, and it ran for every block of every offer."""
    return xmlschema.XMLSchema("general/pulsar/block_schema.xsd")


def validate_block(block):
    try:
        block = re.sub(r'xmlns="[^"]+"', '', block)
        validation_block = f"<root>\n{block}\n</root>"
        result = _block_schema().is_valid(validation_block)
    except FileNotFoundError:
        logger.error("Schema file not found.")
        return False
    except xmlschema.XMLSchemaException as e:
        logger.error(f"XML Schema validation error: {e}")
        return False
    except re.error as e:
        logger.error(f"Regex error: {e}")
        return False
    except ET.ParseError as e:
        logger.error(f"XML parsing error: {e}")
        return False
    return result
