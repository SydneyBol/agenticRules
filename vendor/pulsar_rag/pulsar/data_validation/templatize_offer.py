import re
import requests
from sentence_transformers import SentenceTransformer, util

from common_module.common_utils import get_django_end_point
from general.exception_traces import getExceptionTrace
from general.loggerutils import get_logger
from general.pulsar.program_cache.template_cache import cache_processor
from general.pulsar.id_dependent_attr import get_id_dependent
import defusedxml.ElementTree as ET
model = SentenceTransformer('paraphrase-MiniLM-L6-v2')
logger = get_logger("GRAVTY_AI", "INFO")



def match_word_to_prompt(word, prompt):
    # Encode the word and prompt phrases into embeddings
    word_embedding = model.encode(word, convert_to_tensor=True)
    prompt_embeddings = model.encode(prompt, convert_to_tensor=True)

    # Calculate cosine similarities between the word and each phrase in the prompt
    similarities = util.cos_sim(word_embedding, prompt_embeddings)

    # Find the index of the most similar phrase
    best_match_index = similarities.argmax().item()

    # Get the similarity score of the best match
    best_match_score = similarities[0, best_match_index].item()

    # Threshold for similarity (you can adjust this)
    if best_match_score > 0.75:
        logger.info(best_match_score)
        return prompt[best_match_index]
    else:
        return None


def find_match(prompt_subset, word):
    # transform_match = match_word_to_prompt(word, prompt_subset)
    most_similar_index, similarity_score = cache_processor.most_similar_sentence_transformer(word, prompt_subset)
    logger.info(f"original:{word} match code transformer: {prompt_subset[most_similar_index]}, score:{similarity_score}")
    if similarity_score < 0.75:
        logger.info("Transformer similarity too low. Falling back to levenshtein similarity.")
        most_similar_index, similarity_score = cache_processor.find_most_similar_levenshtein(word, prompt_subset)
        logger.info(f"original: {word} match code levenshtien: {prompt_subset[most_similar_index]}, score:{similarity_score}")
    return prompt_subset[most_similar_index] if similarity_score>0.75 else None


def get_lookup_type(attribute, attributes):
    for attr in attributes:
        if attr['name'] == attribute:
            return attr['lookup_type']


def get_attribute_value(object, attribute, value, program_id, dependent_ids, dependent_id):
    cache = cache_processor.ttl_cache.cache.get(program_id)
    display_value = value
    try:
        if dependent_id:
            headers = cache_processor.programs[program_id].headers
            lov_map = cache_processor.programs[program_id].lovs.lov_map
            attribute_details = cache_processor.programs[program_id].attributes.data.get(object)
            if not attribute_details:
                attribute_details = cache_processor.programs[program_id].custom_attributes.data.get(object)
            lookup_type = attribute_details.get(attribute).lookup_type
            for id in dependent_ids:
                if dependent_id == "sponsor_id":
                    api_url = lov_map[lookup_type]["api_url"].replace('{sponsor_id}',str(id))+f"{'?page_size=3000' if '?' not in lov_map[lookup_type]["api_url"] else '&page_size=3000'}"
                else:
                    api_url = lov_map[lookup_type]["api_url"] + f'?page_id={id}&page_size=1000'
                api_url= f"{get_django_end_point(cache_processor.programs[program_id].secrets)}" +api_url
                raw_data = _lov_page(api_url, headers)

                attribute_data =  {str(item[lov_map[lookup_type]["code"]]): item[lov_map[lookup_type]["display"]] for item in raw_data}
                display_value = attribute_data.get(value)
                if display_value:
                    break

        else:
            attribute_data = cache.get(object).get(attribute)
            display_value = attribute_data.get(value)
        return display_value
    except Exception as e:
        logger.error(f"attribute data is not present for {object} {attribute}: {getExceptionTrace(e)}")
        return value
_lov_pages = {}  # url -> rows: one batch run asks the same LOV page for every value of every offer


def _lov_page(api_url, headers):
    if api_url not in _lov_pages:
        resp = requests.get(api_url, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()
        _lov_pages[api_url] = body.get("results", []) if isinstance(body, dict) else body
    return _lov_pages[api_url]


def get_consecutive_substrings(s, max_words=None):
    """Every run of consecutive words of s, up to max_words long (all lengths when None). A label of n words is
    matched against runs of at most n + 2 words: longer runs never score higher, and all runs of a 60-word prompt
    are 1,830 phrases to embed per value."""
    words = s.split()  # Split the string into words
    longest = len(words) if max_words is None else max_words
    substrings = [" ".join(words[i:j]) for i in range(len(words)) for j in range(i + 1, min(i + longest, len(words)) + 1)]
    return substrings

def get_match_value(object, attribute, values, prompt, program_id, dependent_ids, dependent_id=None):

    if object == 'products_attrs':
        object = 'products'
    display_values = []
    best_matches = []

    for value in values:
        display_value = get_attribute_value(object, attribute, value, program_id, dependent_ids, dependent_id)
        if display_value:
            display_values.append(display_value)
            phrases = get_consecutive_substrings(prompt, len(str(display_value).split()) + 2)
            logger.info(f"{len(phrases)} prompt phrases for {display_value}")
            best_match = find_match(phrases, display_value) if phrases else None
            best_matches.append(best_match if best_match else display_value)

    logger.info(f"best match:{best_matches},original names:{display_values}")
    return best_matches

def open_mutation_tags(xml):
    """<mutation .../> -> <mutation xmlns="...xhtml" ...></mutation>, as the screen writes it. Only mutation tags: the
    old replace(' />', '></mutation>') closed every empty element (an empty <field/>) with </mutation> and broke the
    XML."""
    xml = re.sub(r'<mutation(\s[^>]*?)?\s*/>', r'<mutation\1></mutation>', xml)
    return xml.replace('<mutation ', '<mutation xmlns="http://www.w3.org/1999/xhtml" ')


def templatize_xml(xml, prompt, program_id, sponsor_ids):
    try:
        logger.info(f"starting templatization for xml:{xml}")
        field_names =['value','LOYALTY_ACCOUNT', 'SELECTED_EXPIRE_LOYALTY_ACC', 'SELECTED_PRIVILEGE', 'attribute']
        root = ET.fromstring(xml)
        namespace = root.tag.rsplit('}', 1)[0].strip('{')
        namespace2 = "http://www.w3.org/1999/xhtml"
        logger.info(namespace)
        for block in root.iter(f'{{{namespace}}}block'):
            cobrand_card_value = None
            value = None
            fb_pages = None
            match_values = []
            attribute = None
            tiers_class = None
            dependent_id_match = None
            cobrand_card_match = None
            for child in block:
                logger.info(f'field:{{{namespace}}}field and tag: {child.tag}')
                logger.info(f'mutation:{{{namespace2}}}mutation and tag: {child.tag}')
                if f'{{{namespace}}}field' == child.tag:
                    name = child.attrib.get('name')
                    logger.info(name)
                    if name in field_names and value == child.text:
                        if len(match_values):
                            child.text = f"{{{attribute}: {match_values[0]}}}"
                        for i in range(1, len(match_values)):
                            child.text += f",{{{attribute}: {match_values[i]}}}"
                        logger.info(child.text)
                        break
                    if name == 'LOYALTY_ACCOUNT':
                        logger.info(child.text)
                        match_values = get_match_value("action", "loyalty_account", [child.text], prompt, program_id,
                                                       [])
                        if len(match_values):
                            child.text = f"{{loyalty_account: {match_values[0]}}}"
                    if name == 'fb_pages' and fb_pages == child.text:
                        logger.info(child.text)
                        if len(dependent_id_match):
                            child.text = f"{{fb_pages: {dependent_id_match[0]}}}"
                    if name == 'tiers':
                        logger.info(child.text)
                        tiers_class = child.text
                        match_values = get_match_value("member", 'tiers_class', [child.text], prompt, program_id,
                                                       [])
                        if len(match_values):
                            child.text = f"{{tiers_class: {match_values[0]}}}"
                    if name == 'opperatervalue' and tiers_class:
                        logger.info(f"{child.text}, {tiers_class}")
                        tier_name = child.text
                        match_values = get_match_value("member", 'tier_name', [child.text], prompt, program_id,
                                                       [tiers_class])
                        if len(match_values):
                            child.text = f"{{tier_name: {match_values[0]}}}"
                    if name == 'card_main_option' and cobrand_card_value==child.text:
                        if len(cobrand_card_match):
                            child.text = f"{{cobrand_card: {cobrand_card_match[0]}}}"
                if f'{{{namespace2}}}mutation' == child.tag or f'{{{namespace}}}mutation' == child.tag:
                    type = child.attrib.get('type')
                    cobrand_card_value = child.attrib.get('cardmainoption')
                    logger.info(f"data type:{type}, cobrand card:{cobrand_card_value}")
                    if cobrand_card_value:
                        object = child.attrib.get('object')
                        cobrand_card_match = get_match_value(object, 'cobrand_card', [cobrand_card_value], prompt, program_id, [])
                        child.attrib['cardmainoption'] = f"{{cobrand_card: {cobrand_card_match[0]}}}"
                    if type == 'LOV' or type == 'LOOKUP':
                        mta_type_value = child.attrib.get('mta_type_value')
                        value = child.attrib.get('value')

                        if mta_type_value == 'MA_P':
                            attribute = child.attrib.get('mta_attribute')
                            match_values = get_match_value('member', attribute, [value], prompt, program_id, [])
                            logger.info(f"match_value:{match_values}")
                            if len(match_values):
                                child.attrib['value'] = f"{{{attribute}: {match_values[0]}}}"
                                continue
                        object = child.attrib.get('object')
                        attribute = child.attrib.get('attribute')
                        logger.info(f"object:{object}, attribute:{attribute}, value:{value}")
                        fb_pages = child.attrib.get('fb_pages')
                        dependent_id = get_id_dependent(program_id, object, attribute)
                        dependent_ids =[]
                        if fb_pages:
                            dependent_id_match = get_match_value(object, attribute, fb_pages, prompt, program_id, [])
                            dependent_ids =[fb_pages]
                            child.attrib['fb_pages'] = f"{{fb_pages: {dependent_id_match[0]}}}"
                            dependent_id = "fb_pages"
                        if dependent_id == "sponsor_id":
                            dependent_ids = sponsor_ids
                        values = value.split(',')
                        logger.info(f"object:{object}, attribute:{attribute}, value:{values}")
                        match_values = get_match_value(object, attribute, values, prompt, program_id, dependent_ids, dependent_id)
                        logger.info(f"match_value:{match_values}")
                        if len(match_values):
                            child.attrib['value'] = f"{{{attribute}: {match_values[0]}}}"
                        for i in range(1, len(match_values)):
                            child.attrib['value'] += f",{{{attribute}: {match_values[i]}}}"
                        logger.info(child.attrib.get('value'))
                    elif type == 'undefined' and 'payment_object' in child.attrib:
                        object = child.attrib.get('payment_object')
                        attribute = child.attrib.get('payment_attribute')
                        value = child.attrib.get('value')
                        values = value.split(',')
                        logger.info(f"object:{object}, attribute:{attribute}, value:{values}")
                        match_values = get_match_value("payment", attribute, values, prompt, program_id, [])
                        logger.info(f"match_value:{match_values}")
                        if len(match_values):
                            child.attrib['value'] = f"{{{attribute}: {match_values[0]}}}"
                        for i in range(1, len(match_values)):
                            child.attrib['value'] += f",{{{attribute}: {match_values[i]}}}"
                        logger.info(child.attrib.get('value'))
                    elif type is None:
                        privilegetype = child.attrib.get('privilegetype')
                        loyaltyaccountid = child.attrib.get('loyaltyaccountid')
                        if not loyaltyaccountid:
                            loyaltyaccountid = child.attrib.get('exployaltyacc')
                        dependent_ids= sponsor_ids if privilegetype else []
                        if privilegetype:
                            value = privilegetype
                            match_values = get_match_value("action", 'privilege', [privilegetype], prompt, program_id, dependent_ids,"sponsor_id")
                            logger.info(f"match_value:{match_values}")
                            attribute = 'privilege'
                            if len(match_values):
                                child.attrib['privilegetype'] = f"{{privilege: {match_values[0]}}}"
                        if loyaltyaccountid:
                            value = loyaltyaccountid
                            match_values = get_match_value("action", 'loyalty_account', [loyaltyaccountid], prompt, program_id,
                                                           dependent_ids)
                            logger.info(f"match_value:{match_values}")
                            attribute = 'loyalty_account'
                            if len(match_values):
                                mutation_attribute= 'exployaltyacc' if child.attrib.get('exployaltyacc') else 'loyaltyaccountid'
                                child.attrib[mutation_attribute] = f"{{loyalty_account: {match_values[0]}}}"
                    else:
                        member_balance = child.attrib.get('attribute')
                        match = re.match(r'(\d+)_balance', member_balance) if member_balance else None
                        if match:
                            match_values = get_match_value("member", 'member_balance', [member_balance], prompt,
                                                           program_id,[])
                            logger.info(f"match_value:{match_values}")
                            value = member_balance
                            attribute = 'member_balance'
                            if len(match_values):
                                child.attrib['attribute'] = f"{{member_balance: {match_values[0]} Balance}}"


        updated_xml = ET.tostring(root, encoding='unicode', method='xml')
        updated_xml = updated_xml.replace('xmlns:html="http://www.w3.org/1999/xhtml"', '').replace(':html', '').replace(
            'ns0:', '').replace('html:', '').replace(':ns0', '')
        updated_xml = open_mutation_tags(updated_xml)
        logger.info(updated_xml)

        return updated_xml

    except Exception as e:
        # None, not the input: an offer whose values are not templated would teach its real stores, tiers and
        # accounts as if they were part of the shape; the caller skips it
        logger.error(f"Failed to templatise xml, offer skipped:{getExceptionTrace(e)}")
        return None

