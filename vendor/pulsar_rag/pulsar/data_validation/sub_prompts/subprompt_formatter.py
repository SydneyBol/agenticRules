import re
from typing import List
from general.loggerutils import get_logger

logger = get_logger("GRAVTY_AI")

replacements = {
    'MA_P': 'program_level_tracker',
    'product_offer_attributes': 'product_offer_attributes',
    'MA_O': 'offer_level_tracker->',
    'MA_A': 'activity_level_tracker->'
}


class RuleHandler:
    _base_key = None
    _dynamic_number = None
    _pattern = r'\d+->>'
    _index: int = 0
    _rule: list
    _block_list: list = []
    _block_index: int = None
    rule_str: str = None

    def __init__(self, rule: List[str], blocks: list, block_index: int, attribute_names: dict, rule_index: int = 0):
        self._block_list = blocks
        self._block_index = block_index
        self.rule_str = rule[rule_index]
        self._index = rule_index
        self._rule = rule
        if rule_index < len(rule):
            match = re.search(self._pattern, self.rule_str)
            self._dynamic_number = match.group().split('->>')[0]
            self._base_key = re.sub(self._pattern, '', self.rule_str)
        self._replace_attrib(attribute_names)
        self._attrib_remover()
        self._block_merge(attribute_names)
        self._rule[self._index] = self.rule_str

    def _replace_attrib(self, attribute_names: dict):
        if self._base_key in replacements:
            replacement = replacements[self._base_key].split("->")
            self.rule_str = f"{self._dynamic_number}->>{replacement[0]}"
        if self._base_key == 'MA_O' or self._base_key == 'member_attribute->MA_O':
            pattern = r'<mutation[^>]*\btype="([^"]+)"[^>]*>'
            if self._base_key == 'member_attribute->MA_O':
                pattern = r'<mutation[^>]*\battr_type="([^"]+)'
            matches = re.findall(pattern, self._block_list[self._block_index])
            if matches:
                if self._index + 1 >= len(self._rule):  # the block names no counter (attribute="" in the XML)
                    raise ValueError(f"{self._base_key} counter with no name in {self._rule[0]}")
                next_element = self._rule[self._index + 1]
                block_next_element = next_element.split('->>')[1].strip('"')
                attribute_names[block_next_element] = f'{matches[0]} {block_next_element}'
        elif self._base_key == 'MA_A' or self._base_key == 'member_attribute->MA_A':
            pattern = r'<mutation[^>]*\btype="([^"]+)"[^>]*>'
            if self._base_key == 'member_attribute->MA_A':
                pattern = r'<mutation[^>]*\battr_type="([^"]+)'
            matches = re.findall(pattern, self._block_list[self._block_index])
            if matches:
                if self._index + 1 >= len(self._rule):  # the block names no counter (attribute="" in the XML)
                    raise ValueError(f"{self._base_key} counter with no name in {self._rule[0]}")
                next_element = self._rule[self._index + 1]
                block_next_element = next_element.split('->>')[1].strip('"')
                attribute_names[block_next_element] = f'{matches[0]} {block_next_element}'

    def _attrib_remover(self):
        if self.rule_str.startswith('date_quantity'):
            self._rule[
                self._index] = self._rule[self._index].replace('date_quantity ', '')
            self.rule_str = self._rule[self._index]

    def _block_merge(self, attribute_names: dict):
        match = None
        next_index = self._index + 1
        if next_index < len(self._rule):
            match = re.search(self._pattern, self._rule[next_index]).group().split('->>')
        self.rule_str = re.sub(self._pattern, '', self.rule_str)
        if next_index == len(self._rule):
            return
        # End recursion for merging
        if match and (self._index == len(self._rule) - 1 or
                      int(self._dynamic_number) != int(match[0]) - 1):
            return
        # Recursively merge the blocks
        self.rule_str = (
                self.rule_str
                + " "
                + RuleHandler(rule=self._rule, blocks=self._block_list, rule_index=next_index,
                              attribute_names=attribute_names,
                              block_index=self._block_index).rule_str
        )
        del self._rule[next_index]
