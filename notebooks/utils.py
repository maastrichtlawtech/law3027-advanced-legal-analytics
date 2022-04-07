import inspect
import timeit, datetime
from functools import wraps
from re import finditer, search, escape
from typing import List, Dict, Tuple, Optional, Union, Generator

import random
import pandas as pd
import nltk
nltk.download('wordnet')
nltk.download('punkt')
nltk.download('averaged_perceptron_tagger')

import lexnlp.extract.en as Extractor
from lexnlp.extract.en.entities import nltk_re
from lexnlp.extract.en.dict_entities import DictionaryEntry, DictionaryEntryAlias
from lexnlp.extract.common.annotations.phone_annotation import PhoneAnnotation
from lexnlp.extract.common.annotations.ssn_annotation import SsnAnnotation
from lexnlp.extract.en import (acts, amounts, conditions, constraints, copyright, courts,
                               cusip, dates, definitions, distances, durations, urls,
                               money, percents, pii, ratios, regulations, trademarks)

SUPPORTED_ENTITIES = ['acts', 'amounts', 'companies', 'conditions', 'constraints', 'copyright', 'courts', 'cusip', 'dates', 'definitions', 
                     'distances', 'durations', 'money', 'percents', 'pii', 'ratios', 'regulations', 'trademarks', 'urls']

ENTITIES_CONFIG = {
    "ents": [ent.upper() for ent in SUPPORTED_ENTITIES],
    "colors": {ent.upper(): "#"+''.join([random.choice('0123456789ABCDEF') for j in range(6)]) for ent in SUPPORTED_ENTITIES},
}

def log_step(funct):
    @wraps(funct)
    def wrapper(*args, **kwargs):
        tic = timeit.default_timer()
        result = funct(*args, **kwargs)
        time_taken = datetime.timedelta(seconds=timeit.default_timer() - tic)
        print(f"Just ran '{funct.__name__}' function. Took: {time_taken}")
        return result
    return wrapper

@log_step
def extract(text: str, element_types: Union[str, List[str]]) -> Dict[str, List]:
    """Small utility function built on top of LexNLP to call the functions corresponding to given entity types.
    Args:
        text: the legal text to extract entities from.
        element_types: the type(s) of entity to extract from the given text.
    Returns:
        A dictionary with the extracted entities.
    """
    output = {}
    if isinstance(element_types, str):
        element_types = [element_types]
    for el in element_types:
        assert el in SUPPORTED_ENTITIES, f"Unknown data type: '{el}'"
        module = Extractor.entities.nltk_re if el == 'companies' else getattr(Extractor, el)
        method = getattr(module, f"get_{el}") if el in ['companies', 'courts'] else getattr(module, f"get_{el[:-1]}_annotations" if el not in ['acts', 'copyright', 'cusip', 'money', 'pii'] else f"get_{el}_annotations")
        kwargs = {'text': text, 'return_sources': True} if 'return_sources' in inspect.getfullargspec(method).args else {'text': text}
        kwargs.update({'court_config_list': _load_courts()}) if el == 'courts' else None
        output[el] = method(**kwargs)
    trans_output = _transform_output(output, text)
    return trans_output


def _load_courts() -> Dict[str, str]:
    """Load court data from LexPredict legal dictionaries, including Australian, Canadian and US (federal+state) courts.
    Returns:
        A dictionary with the courts and their ids.
    """
    us_federal_courts_df = pd.read_csv("https://raw.githubusercontent.com/LexPredict/lexpredict-legal-dictionary/master/en/legal/us_courts.csv")
    us_state_courts_df = pd.read_csv("https://raw.githubusercontent.com/LexPredict/lexpredict-legal-dictionary/master/en/legal/us_state_courts.csv")
    ca_courts_df = pd.read_csv("https://raw.githubusercontent.com/LexPredict/lexpredict-legal-dictionary/master/en/legal/ca_courts.csv")
    au_courts_df = pd.read_csv("https://raw.githubusercontent.com/LexPredict/lexpredict-legal-dictionary/master/en/legal/au_courts.csv")

    courts_df = pd.concat([us_federal_courts_df, us_state_courts_df, ca_courts_df, au_courts_df])
    courts_df.reset_index(drop=True, inplace=True)
    courts_df['Court ID'] = courts_df.index + 1

    court_config_data = []
    for _, row in courts_df.iterrows():
        c = DictionaryEntry(id=row["Court ID"], 
                            name=row["Court Name"], 
                            aliases=[DictionaryEntryAlias(alias=al, language='en') for al in (row["Alias"].split(";") if not pd.isnull(row["Alias"]) else [])])
        court_config_data.append(c)
    return court_config_data


def _transform_output(output: Dict[str, Generator], document: str) -> List[Dict]:
    new_output = []
    for key, values in output.items():

        if key == 'acts':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details': {'act_name': v.act_name, 'section': v.section, 'year': v.year}})

        elif key == 'amounts':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details': {'value': v.value}})

        elif key == 'companies':
            for v in values:
                new_output.append({'type': key, 'element': v.name, 'location': v.coords, 'details': {'text': v.text}})

        elif key == 'conditions':
            for v in values:
                coords = (v.coords[1] - len(v.condition), v.coords[1])
                new_output.append({'type': key, 'element': v.condition, 'location': coords, 'details': {'pre': v.pre, 'post': v.post}})

        elif key == 'constraints':
            for v in values:
                coords = (v.coords[1] - len(v.constraint), v.coords[1])
                new_output.append({'type': key, 'element': v.constraint, 'location': coords, 'details':{'pre': v.pre, 'post': v.post}})

        elif key == 'copyright':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'sign': v.sign, 'date': v.date, 'name': v.name}})
        
        elif key == 'courts':
            for v in values:
                element = search(r"^(.*?),\slang:", str(v[1])).group(1).strip()
                value = search(r"\"(.*?)\":", str(v[0])).group(1).strip()
                coords = next(finditer(pattern=escape(element), string=document)).span()
                new_output.append({'type': key, 'element': element, 'location': coords, 'details':{'name': value}})

        elif key == 'cusip':
            for v in values:
                new_output.append({'type': key, 'element': v.code, 'location': v.coords, 'details': {'issuer_id': v.issuer_id, 'issue_id': v.issue_id, 'checksum': v.checksum, 'internal': v.internal, 'tba': v.tba, 'ppn': v.ppn}})
        
        elif key == 'dates':
            for v in values:
                new_output.append({'type': key, 'element': document[v.coords[0]:v.coords[1]].strip(), 'location': v.coords, 'details':{'value': v.date}})

        elif key == 'definitions':
            for v in values:
                new_output.append({'type': key, 'element': v.name, 'location': v.coords, 'details':{'text': v.text}})

        elif key == 'distances':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'amount': v.amount, 'unit': v.distance_type}})

        elif key == 'durations':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'amount': v.amount, 'unit': v.duration_type, 'duration_days': v.duration_days}})

        elif key == 'money':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'amount': v.amount, 'unit': v.currency}})

        elif key == 'percents':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'amount': v.amount, 'unit': v.sign, 'fraction_amount': v.fraction}})

        elif key == 'pii':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'value': v.phone if isinstance(v, PhoneAnnotation) else v.number}})

        elif key == 'ratios':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'left': v.left, 'right':v.right, 'ratio_value':v.ratio}})

        elif key == 'regulations':
            for v in values:
                new_output.append({'type': key, 'element': v.text, 'location': v.coords, 'details':{'source': v.source, 'name': v.name}})

        elif key == 'trademarks':
            for v in values:
                new_output.append({'type': key, 'element': document[v.coords[0]:v.coords[1]].strip(), 'location': v.coords, 'details': {'value': v.trademark}})

        elif key == 'urls':
            for v in values:
                new_output.append({'type': key, 'element': document[v.coords[0]:v.coords[1]].strip(), 'location': v.coords, 'details': {'value': v.url}})

    return new_output
