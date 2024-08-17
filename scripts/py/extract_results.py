import logging
import logging.handlers
import warnings
warnings.filterwarnings('ignore')
import os
from time import time
import multiprocessing as mp

import pandas as pd
import polars as pl
from numerize_denumerize.numerize import numerize
from tqdm import tqdm

from tools.utils.mongodb_utils import get_mongodb_collections, get_one_document_from_mongodb_by_key
from tools.utils.classes import ResultDatabase
from tools.utils.settings import get_all_paths, make_parser
from tools.utils.misc import (
    apply_sloth,
    get_local_time,
    create_token_set,
    logging_setup
)



def worker_result_extractor(chunk):
    global dbname, table_name, dataset, size, blacklist
    resultsdb = ResultDatabase(dbname, table_name)
    resultsdb.open()
    mongoclient, collections = get_mongodb_collections(dataset=dataset, size=size)

    rv = []
    hit = 0

    for (algorithm, mode, (query_id, _, result_ids, algorithm_overlaps)) in tqdm(chunk, leave=False, total=len(chunk), disable=False if os.getpid() % 72 == 0 else True):
        match mode: # problem with previous versions...
            case 'fasttext': mode = 'ft'
            case 'fasttextdist': mode = 'ftdist'
            case '_': mode = mode
        if not result_ids:
            rv.append([query_id, None, algorithm, mode, None, None, None, None, None, None])
            continue
        
        # here we need eval because on the csv file values are stored as strings
        result_ids, algorithm_overlaps = eval(result_ids), eval(algorithm_overlaps)
        
        # retrieve the query information from MongoDB
        doc_table_q = get_one_document_from_mongodb_by_key('_id_numeric', query_id, *collections)
        assert query_id == doc_table_q['_id_numeric']
        table_q = doc_table_q['content']
        numeric_columns_q = doc_table_q['numeric_columns']

        for i, _id_r in enumerate(result_ids):
            # retrieve the result table information from MongoDB
            doc_table_r = get_one_document_from_mongodb_by_key('_id_numeric', _id_r, *collections)
            assert _id_r == doc_table_r['_id_numeric']
            table_r = doc_table_r['content']
            numeric_columns_r = doc_table_r['numeric_columns']

            # if already exists a couple with these ID, take its computed SLOTH overlap
            r_id, s_id = (query_id, _id_r) if query_id <= _id_r else (_id_r, query_id)
            dboverlap, lookuptime = resultsdb.lookup_result_table(r_id, s_id), -1
            
            if dboverlap != None:
                hit += 1
            
            sloth_overlap, sloth_time = (dboverlap, lookuptime) if dboverlap != None else apply_sloth(table_q, table_r, numeric_columns_q, numeric_columns_r, blacklist=blacklist)
            
            if sloth_overlap == -1 and dboverlap == None:
                logging.warning(f"Pair {query_id} - {_id_r} SLOTH failed")

            # the intersection size is used for computing Jaccard Similarity or other metrics like containment, 
            # so compute using the set semantic, since it considers the intersection of the table "basic" values
            set_q = set(create_token_set(table_q, 'set', numeric_columns_q, blacklist=blacklist))
            set_r = set(create_token_set(table_r, 'set', numeric_columns_r, blacklist=blacklist))
            set_intersection_size = len(set_q.intersection(set_r))
            
            bag_q = set(create_token_set(table_q, 'bag', numeric_columns_q, blacklist=blacklist))
            bag_r = set(create_token_set(table_r, 'bag', numeric_columns_r, blacklist=blacklist))
            bag_intersection_size = len(bag_q.intersection(bag_r))

            set_size_q, set_size_r = len(set_q), len(set_r)
            bag_size_q, bag_size_r = len(bag_q), len(bag_r)

            algorithm_overlap = set_intersection_size if mode in ['set', 'ft', 'ftdist'] else bag_intersection_size
            
            jaccard_sim = set_intersection_size / len(set_q.union(set_r))
            multi_jaccard_sim = set_intersection_size / (set_size_q + set_size_r)
            containment = set_intersection_size / set_size_q
            overlap_set_similarity = set_intersection_size / min(bag_size_q, bag_size_r)

            # the area ratio, as stated in SLOTH paper is "the largest overlap 
            # normalized by the area of the smaller table", and we could consider
            # the token set in bag mode as the table area
            area_ratio = sloth_overlap / min(bag_size_q, bag_size_r) 

            rv.append([query_id, _id_r, algorithm, mode, sloth_overlap, algorithm_overlap,
                       
                       set_size_q, set_size_r, set_intersection_size, 
                       bag_size_q, bag_size_r, bag_intersection_size,
                       
                       jaccard_sim, 
                       multi_jaccard_sim, 
                       containment, 
                       overlap_set_similarity, 
                       area_ratio,
                       
                       sloth_time])
        
    mongoclient.close()
    resultsdb.close()
    return hit, rv


final_results = pl.DataFrame(schema={
    'query_id': pl.Int32, 
    'result_id': pl.Int32, 
    'algorithm': pl.String, 
    'mode': pl.String, 
    'sloth_overlap': pl.Int32,
    'algorithm_overlap': pl.Int32,

    'set_size_q': pl.Int32,
    'set_size_r': pl.Int32, 
    'set_intersection_size': pl.Int32,

    'bag_size_q': pl.Int32, 
    'bag_size_r': pl.Int32, 
    'bag_intersection_size': pl.Int32,
    
    'jaccard_sim': pl.Float32,
    'multi_jaccard_sim': pl.Float32,
    'containment': pl.Float32,
    'overlap_set_sim': pl.Float32,
    'area_ratio': pl.Float32,
    
    'sloth_time(s)': pl.Float32
    }
)


def chunks(sequence, chunk_size):
    # Chunks of chunk_size documents at a time.
    for j in range(0, len(sequence), chunk_size):
        yield sequence[j:j + chunk_size]


args = make_parser('test_name', 'num_cpu', 'num_query_samples', 'k', 'dbname', 'dataset', 'size')
test_name =         args.test_name
nworkers =          args.num_cpu
num_query_samples = args.num_query_samples
k =                 args.k
dbname =            args.dbname
dataset =           args.dataset
size =              args.size

test_name = test_name.lower()
num_query_samples = numerize(num_query_samples, asint=True)

TEST_DATASET_DIR, query_file, logfile, _, _, \
    results_base_dir, results_extr_dir, \
        statistics_dir, runtime_stat_file, storage_stat_file = get_all_paths(test_name, dataset, k, num_query_samples)
final_results_file = f'{results_extr_dir}/final_results_k{k}_q{num_query_samples}.csv'


logging_setup(logfile)
logging.info(f'{"#" * 10} {test_name.upper()} - {dataset.upper()} - {size.upper()} - {k} - {num_query_samples} - EXTRACTION {"#" * 10}')

blacklist = {'{"$numberDouble": "NaN"}', 'comment', 'story'} if dataset == 'gittables' else set()
# blacklist = {'{"$numberDouble": "NaN"}'} if dataset == 'gittables' else set()

table_name = f'results_d{dataset}_s{size}' 
# table_name = f'results_d{dataset}_s{size}_blacklist' 



start_analysis = time()
resultsdb = ResultDatabase(dbname, table_name)
resultsdb.open()
# clear the result table (occhio a farlo che poi si perdono i dati già salvati...)
resultsdb.clear()
resultsdb.create_table()

# just to know if the results database is actually useful
hit_rates = []

with mp.Pool(processes=nworkers) as pool:
    for result_file in os.listdir(results_base_dir):
        if result_file.endswith('.raw'): continue
        
        results = pl.read_csv(f'{results_base_dir}/{result_file}')
        algorithm, mode = [x[1:] for x in result_file[:-4].split('_')]
        
        logging.info(f'Extracting results from {result_file} ({algorithm}-{mode})...')
        
        sss = time()
        # keeping the same order leads to longer time
        # usually bad pairs where SLOTH fails in long time are adjacents, so 
        # a shuffle whould be better, even if it need ~x2 memory peak
        work = [(algorithm, mode, row) for row in results.sample(fraction=1.0, shuffle=True).iter_rows()]
        data = []
        
        # smaller chunk-size offer the possibility to better parallelization, because
        # SLOTH often takes time and some processes finish while others still have many computations to do 
        chunksize = max(min(len(work) // nworkers, 1000) // 4, 1)
        logging.info(f'Total work length: {len(work)}, total workers: {nworkers}, chunk-size: {chunksize}. Starting extraction...')
        extr_res = pool.map(worker_result_extractor, chunks(work, chunksize))
        logging.info(f"Completed extraction in {round(time() - sss)}s")
        
        hit = 0
        for h, r in extr_res:
            hit += h
            data += r
            resultsdb.insert_results([[x[0], x[1], x[4]] if x[0] < x[1] else [x[1], x[0], x[4]] for x in r if x[4] != None])

        logging.info(f'hit = {hit} ({round(100 * hit / len(data), 3)}%)')
        hit_rates.append(hit / len(data))
        final_results = pl.concat([final_results, pl.DataFrame(data, schema=final_results.schema, infer_schema_length=10)])

final_results.write_csv(final_results_file)
logging.info(f"Hit rates: {hit_rates}")

# save the statistics about the analysis time
add_header = not os.path.exists(runtime_stat_file)
with open(runtime_stat_file, 'a') as rfw:
    if add_header:
        rfw.write("local_time,algorithm,mode,task,k,num_queriestime\n")

    rfw.write(f"{get_local_time()},analysis,,extraction,{k},{num_query_samples},{round(time() - start_analysis, 3)}\n")

# save statistics about analysis file size
storage_size = os.path.getsize(final_results_file) / (1024 ** 3)

append = os.path.exists(storage_stat_file)
dbsize = pd.DataFrame([['analysis', f'extraction_k{k}_q{num_query_samples}', storage_size]], columns=['algorithm', 'mode', 'size(GB)'])
dbsize.to_csv(storage_stat_file, index=False, mode='a' if append else 'w', header=False if append else True)

resultsdb.close()
