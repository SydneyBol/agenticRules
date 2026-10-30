import base64
from pulsar.data_validation.sub_prompts.blockwiseutils import *
import re
from asyncio import set_event_loop, new_event_loop, get_event_loop, gather
from typing import List, Any, Optional
import asyncio
import defusedxml.ElementTree as ET
import pandas as pd
from general.loggerutils import get_logger

logger = get_logger("GRAVTY_AI", "INFO")
calculator_list = [
    'currency_converter', 'activity_attribute',
    'member_attribute', 'static_date', 'current_date_block', 'static_date_time',
    'static_string_value', 'product_attribute', 'payment_attribute',
    'payment_attribute', 'airport_attribute'
]

block_wise = ['if_generic_adaptive', 'activity_criteria', 'action', 'action_third_party', 'if_payment_attribute']

attr_names = ["OBJECT", "attribute", "operators", "value", "attribute_level",
              "type", "LOYALTY_ACCOUNT", "REWARD_TAGS", "SELECTED_PRIVILEGE",
              "IDENTIFIER", "action_type", "ATTRIBUTE_TYPE", "mta_type_value",
              "mta_attribute", "ATTRIBUTE", "operator", "action_operator", "number",
              "conversion_type", "third_parties_dropdown"]

rule_tags = {'rule', 'rule_line', 'payment_rule'}


class SubPromptGenerator:
    def __init__(self, xml_str: str):
        self.replace_attrib = {}
        self.xml_str = xml_str
        self.subPrompts: Optional[List[Any]] = None
        self._xml = self._is_base64()
        self._nested = bool(nesting_check(format_xml(xml_string=self._xml)))
        self.blockList: List[Any] = self.extract_blocks()
        self._ruleList: List[Any] = self._list_converter()

    def _is_base64(self):
        try:
            if self.xml_str.startswith('<xml'):
                return self.xml_str
            return base64.b64decode(self.xml_str).decode('utf-8')
        except Exception as e:
            logger.error(f"Error occured: {e}")
            raise ValueError("Base64 validation failed")

    async def validate_blocks(self):
        if len(self.blockList) != len(self._ruleList):
            raise ValueError(
                f"Mismatch in block and rule counts: "
                f"blocks={len(self.blockList)}, "
                f"rules={len(self._ruleList)}"
            )
        validation_tasks = [
            validation(block_str=block_str, rule=rule)
            for rule, block_str in zip(self._ruleList, self.blockList)
        ]
        await asyncio.gather(*validation_tasks)

    def _generate_sub_prompts(self):
        attribute_names = {}
        output_lines = [0] * len(self._ruleList)
        set_event_loop(new_event_loop())
        get_event_loop().run_until_complete(
            gather(
                *[sentence_generator(rules, output_lines, index, self.blockList, attribute_names)
                  for index, rules in enumerate(self._ruleList)]
            )
        )
        get_event_loop().close()
        return output_lines, attribute_names

    def extract_blocks(self):
        root = ET.fromstring(self._xml)
        blocks = []
        for elem in root.iter():
            # Remove namespace if present
            tag = elem.tag.split('}', 1)[-1] if '}' in elem.tag else elem.tag
            if tag == 'block' and elem.get('type') in block_wise:
                blocks.append(elem)
        for idx, block in enumerate(blocks):
            updated_xml = ET.tostring(block, encoding='unicode')
            updated_xml = updated_xml.replace(
                'xmlns:html="http://www.w3.org/1999/xhtml" xmlns:ns0="https://developers.google.com/blockly/xml"', '')
            updated_xml = updated_xml.replace('ns0:', '').replace('  ', ' ').replace('html:', '')
            updated_xml = (replace_self_closing_mutation_tag(updated_xml).replace('block xmlns:ns0="w3link"', 'block')
                           .replace('mutation ', 'mutation xmlns="http://www.w3.org/1999/xhtml" ')
                           .replace('xmlns:html="http://www.w3.org/1999/xhtml"', ''))
            match = re.match('(.*?<next>)', updated_xml)
            if match:
                updated_xml = match.group(1).replace('<next>', '</block>')
            blocks[idx] = updated_xml

        return blocks

    def _list_converter(self) -> List[Any]:
        rule_list = self._xml_to_list()
        return rule_list

    def subprompt_creator(self):
        self.subPrompts, self.replace_attrib = self._generate_sub_prompts()

    @staticmethod
    def _process_block(block_element: ET, namespace: str):
        try:
            block_info = []
            block_header_added = False
            count = 0
            for index, child in enumerate(block_element):
                is_value_child_tag = child.tag == f'{{{namespace}}}value'
                is_field_child_tag = child.tag == f'{{{namespace}}}field'
                is_rule_tag = False
                if is_field_child_tag:
                    is_rule_tag = child.attrib['name'] == 'IDENTIFIER'
                # Single value block
                if is_field_child_tag and child.text is None and child.attrib['name']:
                    return block_info
                # Incomplete block return
                if is_field_child_tag and child.text is None:
                    return None

                if len(block_info) > 0 and ('MA_O' in block_info[-1] or 'MA_A' in block_info[-1]):
                    child.text = f'"{child.text}"'
                if is_value_child_tag and child.attrib['name'] == 'vector_counter_namespace':
                    block_info[-1] = f"{block_info[-1]} {child.attrib['name']}"
                if is_field_child_tag and not is_rule_tag:
                    if block_element.attrib['type'] in block_wise and not block_header_added:
                        block_header_added = True
                        block_info.append(f"{block_element.attrib['type']}  :::  {count}->>{child.text}")
                    elif block_element.attrib['type'] in calculator_list:
                        block_info.append(f"{block_element.attrib['type']}->{count}->>{child.text}")
                    else:
                        if child.attrib['name'] not in attr_names:
                            block_info.append(f"{child.attrib['name']} {count}->>{child.text}")
                        else:
                            block_info.append(f"{count}->>{child.text}")
                            if ('in' == child.text or 'not_in' == child.text) and child.attrib['name'] == 'operators':
                                block_info.append(f'{count}->>{{xyz}}')
                elif (is_field_child_tag and block_element.attrib['type'] in rule_tags) and not \
                        (block_info and block_info[-1] == f"{block_element.attrib['type']}  :::  IDENTIFIER"):
                    block_info.append(f"{block_element.attrib['type']}  :::  IDENTIFIER")
                if len(block_info) > 0:
                    count += 1
            return block_info
        except IndexError as e:
            logger.error(f"Error occurred in extractor: {e}")

    def _attrib_list_generation(self):
        attributes_ordered = []
        root = ET.fromstring(self._xml)
        namespace = root.tag.rsplit('}', 1)[0].strip('{')

        for block_str in root.iter(f'{{{namespace}}}block'):
            sub_blocks = self._process_block(block_str, namespace)
            if sub_blocks is None:  # an incomplete block: the offer gives no usable lines
                raise ValueError(f"incomplete block {block_str.attrib.get('type')} id={block_str.attrib.get('id')}")
            attributes_ordered.extend(sub_blocks)
        return attributes_ordered

    def _xml_to_list(self):
        rule_type = None
        rules_list = []
        rule_list = []
        rule_idx = 0
        attributes_ordered = self._attrib_list_generation()

        for data in attributes_ordered:
            if 'activity_criteria  :::' in data:
                rule_idx += 1
                rule_type = f'Filter_{rule_idx},,,'
            elif ':::  IDENTIFIER' in data:
                rule_idx += 1
                rule_type = f"{data.split(' ::: ')[0].strip()}_{rule_idx},,,"

            if data.split('  :::  ')[0].strip() in block_wise:
                if rule_list:
                    rules_list.append(rule_list)
                rule_list = [rule_type, data.replace('  :::  ', ':')]

            elif ':::  IDENTIFIER' not in data:
                rule_list.append(data)

        if rule_list:
            rules_list.append(rule_list)

        return rules_list


def xml_to_subprompt(xml_str: str):
    """Process xml from input to generate subPrompts"""
    try:
        df = pd.DataFrame()
        sub_prompt_generator = SubPromptGenerator(xml_str=xml_str)
        if re.search(BLUEPRINT, sub_prompt_generator._xml):
            raise ValueError("a blueprint: placeholders, not an offer's rules")
        asyncio.run(sub_prompt_generator.validate_blocks())
        sub_prompt_generator.subprompt_creator()
        df["Lines"] = sub_prompt_generator.subPrompts
        df["Blocks"] = sub_prompt_generator.blockList
        replace_attrib = sub_prompt_generator.replace_attrib
        if replace_attrib:
            df["Lines"] = df["Lines"].map(lambda s: replace_names(s, replace_attrib))
            df["Blocks"] = df["Blocks"].map(lambda s: replace_names(s, replace_attrib))
        if len(df) == 0:
            raise Exception("SubPrompts and Blocks couldn't be Generated.")
        df = df[~df['Blocks'].str.contains(DISABLED)]
        df = df.apply(in_operator_fix, axis=1)
        final_output = []
        for index, row in df.iterrows():
            final_output.append((row["Lines"], row["Blocks"]))
        return final_output if final_output else None
    except Exception as e:
        logger.info(f"Error occurred: {e}")

# An offer template's placeholders (blueprint_variable blocks, blueprinttypes / blueprint_type_input mutations)
BLUEPRINT = r'type="blueprint_variable"|blueprinttypes="|blueprint_type_input="'

# A block the manager disabled is not part of the rules: Blockly < 10 writes disabled="true", Blockly 10+ (the current
# UI) writes disabled-reasons="..."
DISABLED = r'disabled="true"|disabled-reasons="'


def replace_names(text, names):
    """Each counter/attribute name -> its typed form ({"Total spend": "NUMBER Total spend"}), literally (names may hold
    regex characters such as brackets), whole names only and in one pass, so a name inside a longer one or inside an
    earlier replacement is not replaced again."""
    pattern = "|".join(re.escape(k) for k in sorted(names, key=len, reverse=True))
    return re.sub(rf"(?<![\w])(?:{pattern})(?![\w])", lambda m: names[m.group(0)], text)


def replace_self_closing_mutation_tag(xml_content):
    pattern = r'(<mutation\s+[^>]+?)\s*/>'
    modified_string = re.sub(pattern, r'\1></mutation>', xml_content)
    return modified_string
