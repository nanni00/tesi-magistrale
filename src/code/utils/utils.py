import os
import re
from time import time 
import numpy as np
import pandas as pd
import nltk

# tr = str.maketrans('', '', string.punctuation.replace('-', '')) # to keep minus sign

try:
    # nltk.data.find('stopwords', f"{os.environ['HOME']}/nltk_data")
    from nltk.corpus import stopwords
except LookupError:
    nltk.download('stopwords')
    from nltk.corpus import stopwords
    
stopwords_set = set(stopwords.words('english'))



def print_info(**dec_kwargs):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if 'msg_before' in dec_kwargs: print(dec_kwargs['msg_before'])
            start = time()
            
            results = func(*args, **kwargs)
            
            end = time()

            if 'time' in dec_kwargs: 
                print(f'Elapsed time: {round(end - start, 3)}s')
            if 'msg_after' in dec_kwargs: print(dec_kwargs['msg_after'])
            return results
        return wrapper
    return decorator


def round_to(n, precision):
    if n >= 0 or n < 0:
        correction = 0.5 if n >= 0 else -0.5
        return int(n / precision + correction) * precision
    else:
        return n


def round_to_05(n):
    return float(format(round_to(n, 0.05), ".2f"))


def rebuild_table(table):
    return pd.DataFrame(
        data=[
            [entry_data['text'] 
             for entry_data in entry]
            for entry in table['tableData']
        ],
        columns=table['tableHeaders'][0]
        )


def my_tokenizer(s: str, remove_numbers=False):
    s = str(s)
    if not remove_numbers:
        return [            
            x for x in re.findall(r'\b([a-zA-Z]+|\d{1}|\d{2}|\d{3}|\d{4})\b', s) 
            if x not in stopwords_set
        ]
    else:
        return [
            x for x in re.findall(r'[a-zA-Z]+', s)
            if x not in stopwords_set
        ]


def cosine_similarity(a1:np.array, a2:np.array):
    return np.dot(a1, a2) / (np.linalg.norm(a1) * np.linalg.norm(a2))
