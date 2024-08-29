import pickle

import polars as pl
import pymongo
import pymongo.collection

from tools.utils.basicconfig import MONGODB_DATASETS, DATASETS_SIZES


def get_mongodb_collections(dataset:str, size:str) -> tuple[pymongo.MongoClient, list[pymongo.collection.Collection]]:
    assert dataset in MONGODB_DATASETS
    assert size in DATASETS_SIZES
    
    mongoclient = pymongo.MongoClient(directConnection=True)
    collections = []

    if 'optitab' in mongoclient.list_database_names():
        match dataset:
            case 'wikiturlsnap':
                collections.append('mongoclient.optitab.turl_training_set')
                collections.append('mongoclient.sloth.latest_snapshot_tables')
            case 'gittables':
                collections.append('mongoclient.sloth.gittables')
            case '_':
                raise ValueError(f'Unknown dataset: {dataset}')
    elif 'datasets' in mongoclient.list_database_names():
        match dataset:
            case 'wikitables':
                collections.append('mongoclient.datasets.wikitables')
            case 'gittables':
                collections.append('mongoclient.datasets.gittables')
    else:
        raise ValueError('Current MongoDB not configured')
    
    collections = [eval(c + '_small' if size == 'small' else c, {'mongoclient': mongoclient}) for c in collections]
    return mongoclient, collections



def get_document_from_mongodb_by_numeric_id(id_numeric, *collections:pymongo.collection.Collection):
    for collection in collections:
        if (document := collection.find_one({'_id_numeric': id_numeric})) != None:
            return document

def get_one_document_from_mongodb_by_key(*args):
    raise DeprecationWarning()



class SimpleDataLakeHelper:
    """
    A simple class that helps to manage different data lake sources.
    Since the original datasets GitTables and WikiTurlSnap are stored in MongoDB
    and the SANTOS Large data lake as a CSVs folder, this structure avoids boiler plates code (sperem) """
    def __init__(self, datalake_location:str, *args):
        self.datalake_location = datalake_location
        self._dataset = None
        self._size = None
        self._mapping_id_path = None
        self._numeric_columns_path = None
        
        match self.datalake_location:
            case 'mongodb':
                self._mongoclient, self._collections = get_mongodb_collections(*args[:2])
                self._dataset = args[0]
                self._size = args[1]
            case _:
                self._mapping_id_path = args[2]
                self._numeric_columns_path = args[3]
                mapping_id_path, numeric_columns_path = args[2:]
                with open(mapping_id_path, 'rb') as fr:
                    self._mapping_id = pickle.load(fr)
                with open(numeric_columns_path, 'rb') as fr:
                    self._numeric_columns = pickle.load(fr)
                
    def get_table_by_numeric_id(self, numeric_id):
        """Return a dictionary with fields:
            - _id_numeric
            - content
            - numeric columns"""
        match self.datalake_location:
            case 'mongodb':
                if doc := get_document_from_mongodb_by_numeric_id(numeric_id, *self._collections):
                    content = doc['content']
                    numeric_columns = doc['numeric_columns']
                else:
                    return None
            case _:
                try:
                    content = pl.read_csv(f'{self.datalake_location}/{self._mapping_id[numeric_id]}.csv', has_header=False, infer_schema_length=0, encoding='latin1').rows()
                    numeric_columns = self._numeric_columns[numeric_id]
                except KeyError:
                    return None
        
        return {'_id_numeric': numeric_id, 'content': content, 'numeric_columns': numeric_columns}

    def get_number_of_tables(self):
        match self.datalake_location:
            case 'mongodb':
                return sum(c.count_documents({}, hint='_id_') for c in self._collections)
            case _:
                return len(self._mapping_id)

    def scan_tables(self):
        match self.datalake_location:
            case 'mongodb':
                for collection in self._collections:
                    for doc in collection.find({}):
                        yield {'_id_numeric': doc['_id_numeric'], 'content': doc['content'], 'numeric_columns': doc['numeric_columns']}
            case _:
                for _id_numeric in self._mapping_id.keys():
                    yield self.get_table_by_numeric_id(_id_numeric)

    def copy(self):
        return SimpleDataLakeHelper(self.datalake_location, self._dataset, self._size, self._mapping_id_path, self._numeric_columns_path)

    def close(self):
        match self.datalake_location:
            case 'mongodb':
                self._mongoclient.close()
            case _:
                pass



