import logging
import os
import sys
import shutil
import argparse

import pandas as pd
from numerize_denumerize.numerize import numerize

from tools.utils.settings import DefaultPath as defpath
from tools.utils.utils import (
    get_local_time,
    get_query_ids_from_query_file, 
    sample_queries,
    logging_setup
)
from tools.utils.mongodb_utils import get_mongodb_collections

from tools import josie, lshforest, embedding #, neo4j_graph



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--test-name', 
                        type=str, required=True,
                        help='the test name. It will be always considered as a lower-case string')
    parser.add_argument('-a', '--algorithm',
                        required=False, default='josie',
                        choices=['josie', 'lshforest', 'embedding', 'graph'])
    parser.add_argument('-m', '--mode', 
                        required=False, default='set',
                        choices=['set', 'bag', 'fasttext', 'fasttextdist', 'tabert', 'neo4j'],
                        help='the specific version of the algorithm. Note that an algorithm doesn\'t support all the available modes: for example, \
                            if "algorithm"="embedding", the only accepted mode is "fasttext"')
    parser.add_argument('-k', 
                        type=int, required=False, default=5,
                        help='the K value for the top-K search')
    parser.add_argument('-t', '--tasks', 
                        required=False, nargs='+',
                        choices=['all', 
                                'data-preparation',
                                'sample-queries', 
                                'query'], 
                        help='the tasks to do')
    parser.add_argument('--num-query-samples', 
                        nargs='+', required=False, default=1000,
                        help='the number of tables that will be sampled from the collections and that will be used as query id for JOSIE (the actual number) \
                            may be less than the specified one due to thresholds tables parameter')
    parser.add_argument('--num-cpu', 
                        type=int, required=False, default=min(os.cpu_count(), 96),
                        help='number of CPUs that will be used in the experiment')
    parser.add_argument('-u', '--user-interaction',
                        required=False, action='store_true',
                        help='specify if user should be asked to continue if necessary')
    # parser.add_argument('--query-file', 
    #                     required=False, type=str, 
    #                     help='an absolute path to an existing file containing the queries which will be used for JOSIE tests')
    parser.add_argument('--size', 
                        type=str, choices=['standard', 'small'],
                        required=False, default='standard',
                        help='works on small collection versions (only for testing)')
    parser.add_argument('--dataset', 
                        required=True, choices=['wikipedia', 'gittables'])

    # clean task
    parser.add_argument('--clean', 
                        required=False, action='store_true', 
                        help='remove PostgreSQL database tables and other big files, such as the LSH Forest index file')

    # JOSIE specific arguments
    parser.add_argument('-d', '--dbname', 
                        required=False, default='user',
                        help='the PostgreSQL database where will be uploaded the data used by JOSIE. It must be already running on the machine')
    parser.add_argument('--token-table-on-memory',
                        required=False, action='store_true')

    # LSH Forest specific arguments
    parser.add_argument('--forest-file', 
                        required=False, type=str, 
                        help='the location of the LSH Forest index file that will be used for querying. If it does not exist, \
                            a new index will be created at that location.')
    parser.add_argument('--num-perm', 
                        required=False, type=int, default=128,
                        help='number of permutations to use for minhashing')
    parser.add_argument('-l', 
                        required=False, type=int, default=8,
                        help='number of prefix trees (see datasketch.LSHForest documentation)')
    
    # Embedding versions specific arguments
    # None yet
    
    # Neo4j graph specific arguments
    parser.add_argument('--neo4j-user', 
                        required=False, type=str, default='neo4j')
    parser.add_argument('--neo4j-password', 
                        required=False, type=str, default='12345678')

    args = parser.parse_args()
    test_name =         args.test_name
    algorithm =         args.algorithm
    mode =              args.mode
    tasks =             args.tasks if args.tasks else []
    k =                 args.k
    
    nsamples =          args.num_query_samples
    user_interaction =  args.user_interaction
    neo4j_user =        args.neo4j_user
    neo4j_passwd =      args.neo4j_password
    num_cpu =           args.num_cpu
    size =              args.size
    dataset =           args.dataset

    # JOSIE
    user_dbname =       args.dbname
    token_table_on_mem =   args.token_table_on_memory

    # LSHForest
    num_perm =          args.num_perm
    l =                 args.l

    test_name = test_name.lower()
    nsamples = [int(nsamples)] if type(nsamples) == int else [int(n) for n in nsamples]

    # check configuration
    if (algorithm, mode) not in {
        ('josie', 'set'),       
        ('josie', 'bag'), 
        ('lshforest', 'set'),       
        ('lshforest', 'bag'),
        ('embedding', 'fasttext'),
        ('embedding', 'fasttextdist'),
        ('embedding', 'tabert'),
        # ('graph', 'neo4j')
        }:
        sys.exit(1)

    # filtering only those tables that have very few cells (<10)
    tables_thresholds = {
        'min_row':      5,
        'min_column':   2,
        'min_area':     0,
        'max_row':      999999,
        'max_column':   999999,
        'max_area':     999999,
    }

    # tasks to complete in current run
    DATA_PREPARATION =          'data-preparation' in tasks or 'all' in tasks
    SAMPLE_QUERIES =            'sample-queries' in tasks or 'all' in tasks
    QUERY =                     'query' in tasks or 'all' in tasks
    CLEAN =                     args.clean

    # output files and directories
    ROOT_TEST_DIR =             defpath.data_path.tests + f'/{test_name}'
    TEST_DATASET_DIR =          ROOT_TEST_DIR + f'/{dataset}'
    query_files =               {n: TEST_DATASET_DIR + f'/query_{numerize(n, asint=True)}.json' for n in nsamples}
    logfile =                   TEST_DATASET_DIR + '/logging.log'

    # LSH-Forest stuff
    forest_dir =                TEST_DATASET_DIR + f'/lshforest' 
    forest_file =               forest_dir + f'/forest_m{mode}.json' if not args.forest_file else args.forest_file
    
    # embedding stuff
    embedding_dir =             TEST_DATASET_DIR + '/embedding'
    cidx_file =                 embedding_dir + f'/col_idx_mfasttext.index' if mode in ['fasttext', 'fasttextdist'] else embedding_dir + f'/col_idx_m{mode}.index' 

    # results stuff
    results_base_dir =          {n: TEST_DATASET_DIR + f'/results/base/k{k}_q{numerize(n, asint=True)}' for n in nsamples}
    results_extr_dir =          TEST_DATASET_DIR + '/results/extracted'
    topk_results_files =        {n: results_base_dir[n] + f'/a{algorithm}_m{mode}.csv' for n in nsamples}

    # statistics stuff
    statistics_dir =            TEST_DATASET_DIR  + '/statistics'
    runtime_stat_file =         statistics_dir + '/runtime.csv'     
    db_stat_file =              statistics_dir + '/db.csv'
    storage_stat_file =         statistics_dir + '/storage.csv'


    # a set of tokens that will be discarded when working on a specific dataset
    # 'comment' and 'story' are very frequent in GitTables, should they be removed? 
    blacklist = {'{"$numberDouble": "NaN"}', 'comment', 'story'} if dataset == 'gittables' else set()
    # blacklist = {'{"$numberDouble": "NaN"}'} if dataset == 'gittables' else set()

    # a list containing information about timing of each step
    runtime_metrics = []

    # the MongoDB collections where initial tables are stored
    mongoclient, collections = get_mongodb_collections(dataset=dataset, size=size)

    # the prefix used in the PostgreSQL database tables (mainly for JOSIE)
    table_prefix = f'{test_name}_d{dataset}_m{mode}'

    # selecting the right tester accordingly to the specified algorithm and mode
    tester = None
    default_args = (mode, dataset, size, tables_thresholds, num_cpu, blacklist)
    match algorithm:
        case 'josie':
            tester = josie.JOSIETester(*default_args, user_dbname, table_prefix, db_stat_file)
        case 'lshforest':
            tester = lshforest.LSHForestTester(*default_args, forest_file, num_perm, l, collections)
        case 'embedding':
            model_path = defpath.model_path.fasttext + '/cc.en.300.bin' if mode in ['fasttext', 'fasttextdist'] else defpath.model_path.tabert + '/tabert_base_k3/model.bin'
            tester = embedding.EmbeddingTester(*default_args, model_path, cidx_file, collections)


    if DATA_PREPARATION or QUERY or SAMPLE_QUERIES:
        if os.path.exists(TEST_DATASET_DIR) and user_interaction:
            if input(get_local_time(), f'Directory {TEST_DATASET_DIR} already exists: delete it (old data will be lost)? (yes/no) ') in ('y', 'yes'):
                shutil.rmtree(TEST_DATASET_DIR)
        for directory in [TEST_DATASET_DIR, statistics_dir, *results_base_dir.values(), results_extr_dir, forest_dir, embedding_dir]:
            if not os.path.exists(directory): 
                logging.info(f'Creating directory {directory}...')
                os.makedirs(directory)
    
    logging_setup(logfile=logfile)
    
        
    if DATA_PREPARATION:
        logging.info(f'{"#" * 10} {test_name.upper()} - {algorithm.upper()} - {mode.upper()} - {k} - {dataset.upper()} - {size.upper()} - DATA PREPARATION {"#" * 10}')
        try:    
            exec_time, storage_size = tester.data_preparation()
            runtime_metrics.append((('data_preparation', None), exec_time, get_local_time()))
            append = os.path.exists(storage_stat_file)
            dbsize = pd.DataFrame([[algorithm, mode, storage_size]], columns=['algorithm', 'mode', 'size(GB)'])
            dbsize.to_csv(storage_stat_file, index=False, mode='a' if append else 'w', header=False if append else True)
        except Exception as e:
            logging.error(f"Error on data preparation: exception message {e.args}")
            

    if SAMPLE_QUERIES:
        logging.info(f'{"#" * 10} {test_name.upper()} - {algorithm.upper()} - {mode.upper()} - {k} - {dataset.upper()} - {size.upper()} - SAMPLING QUERIES {"#" * 10}')
        for n, query_file in query_files.items():
            try:
                if not os.path.exists(query_file):
                    num_samples = sample_queries(query_file, n, tables_thresholds, *collections)
                    logging.info(f'Sampled {num_samples} query tables (required {n}).')
                else:
                    logging.info(f'Query file for {n} queries already present.')
            except Exception as e:
                logging.error(f"Error on sampling queries: n={n}, query_file={query_file}, exception message {e.args}")

    if QUERY:
        logging.info(f'{"#" * 10} {test_name.upper()} - {algorithm.upper()} - {mode.upper()} - {k} - {dataset.upper()} - {size.upper()} - QUERY {"#" * 10}')
        for n, query_file in query_files.items():
            try:
                query_ids = get_query_ids_from_query_file(query_file)
                topk_results_file = topk_results_files[n]
                query_mode = 'naive' if mode == 'fasttext' else 'distance'

                exec_time = tester.query(topk_results_file, k, query_ids, results_directory=results_base_dir[n], token_table_on_memory=token_table_on_mem, query_mode=query_mode)
                runtime_metrics.append((('query', numerize(len(query_ids), asint=True)), exec_time, get_local_time()))
            except Exception as e:
                logging.error(f"Error on query: n={n}, query_file={query_file}, exception message {e.args}")
                raise Exception()
            
            
    if DATA_PREPARATION or QUERY or SAMPLE_QUERIES:
        add_header = not os.path.exists(runtime_stat_file)
        with open(runtime_stat_file, 'a') as rfw:
            if add_header:
                rfw.write("local_time,algorithm,mode,task,k,num_queries,time(s)\n")
            for ((t_name, num_queries), t_time, t_loctime) in runtime_metrics:
                rfw.write(f"{t_loctime},{algorithm},{mode},{t_name},{k},{num_queries},{t_time}\n")


    if CLEAN:
        logging.info(f'{"#" * 10} {test_name.upper()} - {algorithm.upper()} - {mode.upper()} - {dataset.upper()} - {size.upper()} - CLEANING {"#" * 10}')
        tester.clean()
        
    if mongoclient:
        mongoclient.close()


    logging.info('All tasks have been completed.')
