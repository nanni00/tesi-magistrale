from collections import Counter


# Restructure the table as the list of its columns, ignoring the headers
def parse_table(table, num_cols, num_headers):
    return [[row[i] for row in table[num_headers:]] for i in range(num_cols)]            

# Convert a table into a bag of tuples
def to_bag(table):
    counter = dict()
    tuples = [tuple([col[i] for col in table]) for i in range(0, len(table[0]))]
    for i in range(0, len(tuples)):
        if tuples[i] in counter:
            counter[tuples[i]] += 1
        else:
            counter[tuples[i]] = 0
        tuples[i] += (counter[tuples[i]],)
    return set(tuples)


# Convert a table into a bag of tuples using counter objects
def to_bag_counter(table):
    counter = Counter()
    for t in [tuple([col[i] for col in table]) for i in range(0, len(table[0]))]:
        counter[t] += 1
    return counter


if __name__ == '__main__':
    table = [[1, 'a', 'ggg'], [2, 'b', 'fff'], [3, 'c', 'ttt']]
    
    num_headers = 0
    num_cols = len(table[0])
    mycol = [[row[i] for row in table[num_headers:]] for i in range(num_cols)]
    
    import pprint
    pprint.pprint(mycol)
    
    col = parse_table(table, num_headers, num_cols)
    pprint.pprint(mycol)