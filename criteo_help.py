import re
import regex

def translate_sql(q, src='presto', dest='hive', verbose=True):
    """
    Translate queries between Presto, Hive and Vertica SQL.
    """
    
    # 0. Preliminary steps
    
    # Remove inline comments (comments which are always associated with a newline
    # character), but keep them in memory in order to add them back at the end
    newlines_and_comments = [c[0] for c in re.findall('((--.+)*\n)', q)] # store
    newlines = [c[0] for c in re.findall('\n', q)] # store
    q = re.sub('--.+?(?=\n)', '', q) # remove (but keep newline characters)
    
    # Lower text and initialize replacements counter
    q = q.lower()
    replacements = []
    
    # Show warnings if needed
    if ('||' in q) | ('concat_ws' in q) | ('array_join' in q):
        print("Warning: Translation doesn't support all concatenation operations yet.")
    if len(re.findall(r'\b(map|transform|map_from_entries)\b', q)) > 0:
        print("Warning: Translation doesn't support all presto mapping functions yet.")
    if len(re.findall(r'\bnamed_struct\b', q)) > 0:
        print("Warning: Hive's NAMED_STRUCT doesn't have an equivalent in Presto and Vertica.")
    
    # 1. From specific languages
    
    if src == 'hive':
        
        # First, hive specific & presto / vertica common
        
        # lateral view -> unnest, with realiasing if needed (if we unnest an array vs. a struct)
        r = r'lateral\s+view\s+explode\s*(\([\S\s]+?\))\s+(\w+)\s+as\s+(\w+)'
        search = re.findall(r, q)
        col_aliases = [s[2] for s in search]
        search = [re.findall(r'{}\.'.format(a), q) for a in col_aliases]
        counter = 0
        counter_realiasing = 0
        for s in search:
            if len(s) > 0:
                q = re.sub(r, r'cross join unnest\1 as \3', q, count=1)
                counter += 1
            else:
                q = re.sub(r, r'cross join unnest\1 as \2 (\3)', q, count=1)
                counter_realiasing += 1
        replacements.append(['lateral view explode -> cross join unnest', counter])
        replacements.append(['lateral view explode -> cross join unnest with realiasing', counter_realiasing])
        
        # pmod -> mod
        r = r'\bpmod\s*\('
        replacements.append(['pmod -> mod', len(re.findall(r, q))])
        q = re.sub(r, r'mod(', q)
        
        # string -> varchar
        r = r'\bstring\b'
        replacements.append(['string -> varchar', len(re.findall(r, q))])
        q = re.sub(r, r'varchar', q)
        
        # add "" when col name starts with numeric
        r = r'(?<=\s)(\b\d[A-Za-z_]+\b)'
        replacements.append(['add "" when col name starts with numeric', len(re.findall(r, q))])
        q = re.sub(r, r'"\1"', q)
        
        # ` -> "
        r = r'`'
        replacements.append(['` -> "', len(re.findall(r, q))])
        q = re.sub(r, r'"', q)
        
        # array() -> array[]
        r = r"(array)\s*(\(((?>[^()]++|(?2))*)\))"
        subcounter = 0
        while len(regex.findall(r, q)) > 0:
            subcounter += len(regex.findall(r, q))
            q = regex.sub(r, r'\1[\3]', q) # with regex module for nested brackets (recursive)
        replacements.append(['array() -> array[]', subcounter])
        
        # to_date -> date
        r = r'\bto_date\s*\('
        replacements.append(['to_date() -> date()', len(re.findall(r, q))])
        q = re.sub(r, r'date(', q)
        
        # add '' to interval quantity (if needed)
        r = r'(?<=\binterval\b\s)(\s*\d+)'
        replacements.append(["add '' to interval quantity", len(re.findall(r, q))])
        q = re.sub(r, r"'\1'", q)
        
        # rlike -> regexp_like()
        r = r"(\w+)\s*(\((?>[^()]++|(?2))*\))*\s+(?:rlike)\s+('[\S\s]*')"
        subcounter = 0
        while len(regex.findall(r, q)) > 0:
            subcounter += len(regex.findall(r, q))
            q = regex.sub(r, r"regexp_like(\1\2, \3)", q)
        replacements.append(['rlike -> regexp_like()', subcounter])
        
        # extract(part from str) -> extract(part from date)
        search = regex.findall(r'\b(extract)\s*(\(((?>[^()]++|(?2))*)\))*', q)
        replacements.append(['cast inside of extract() to date', len(search)])
        search_corrected = [s[2].split('from') for s in search]
        search_corrected = [' from '.join([search_el[0].strip(), 'date(' + search_el[1].strip() + ')']) for search_el in search_corrected]
        search_corrected = list(zip([s[2] for s in search], search_corrected))
        for s in search_corrected:
            q = q.replace(s[0], s[1])
        
        # Then, hive specific & presto specific
        if dest == 'presto':            
            
            # unix_timestamp() -> to_unixtime()
            r = r'\bunix_timestamp\s*\('
            replacements.append(['unix_timestamp() -> to_unixtime()', len(re.findall(r, q))])
            q = re.sub(r, r'to_unixtime(', q)
        
            # size() -> cardinality()
            r = r'\bsize\s*\('
            replacements.append(['size() -> cardinality()', len(re.findall(r, q))])
            q = re.sub(r, r'cardinality(', q)
            
            # collect_list() -> array_agg()
            r = r'\bcollect_list\s*\('
            replacements.append(['collect_list() -> array_agg()', len(re.findall(r, q))])
            q = re.sub(r, r'array_agg(', q)
            
            # collect_set() -> array_agg(distinct)
            r_window = r'((collect_set)\s*(\((?>[^()]++|(?2))*\))[\S\s]+?(over)\s*(\((?>[^()]++|(?2))*\)))'
            r_collect_set = r'\bcollect_set\s*\('
            # first translate the cases with window function
            search = regex.findall(r_window, q)
            if len(search) > 0:
                collect_sets = [c[0] for c in search]
                collect_sets_translated = [regex.sub(r_window, r'array_distinct(\1)', c) for c in collect_sets]
                collect_sets_translated = [re.sub(r_collect_set, r'array_agg(', c) for c in collect_sets_translated]
                for c, c_translated in zip(collect_sets, collect_sets_translated):
                    q = q.replace(c, c_translated)
                replacements.append(['collect_set() -> array_distinct(array_agg() over window)', len(search)])
            # then translate the normal cases
            replacements.append(['collect_set() -> array_agg(distinct)', len(re.findall(r_collect_set, q))])
            q = re.sub(r_collect_set, r'array_agg(distinct ', q)
            
            # datediff -> date_diff + add unit + cast inside as date
            # To cast the inside as date we need to split it, but splitting at ',' can be
            # a problem if the expression includes another function. Actually, we need to
            # split only if there is no '()' left of the comma -> I use a trick with ';'
            search = regex.findall(r"\b(datediff)\s*(\(((?>[^()]++|(?2))*)\))*", q)
            replacements.append(['datediff() -> date_diff() + add unit + cast inside as date', len(search)])
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(['date(' + s.strip() + ')' for s in search_el]) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])
            q = re.sub(r'\b(datediff)\s*\(', r"date_diff('day', ", q)
            
            # date_add(str, value) -> date_add('day', value, date)
            search = regex.findall(r"\b(date_add)\s*(\(((?>[^()]++|(?2))*)\))*", q)
            replacements.append(["date_add(str, value) -> date_add('day', value, date)", len(search)])
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(["'day'", search_el[1].strip(), 'date(' + search_el[0].strip() + ')']) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])

            # date_sub(str, value) -> date_add('day', -value, date)
            search = regex.findall(r'\b(date_sub)\s*(\(((?>[^()]++|(?2))*)\))*', q)
            replacements.append(["date_sub(str, value) -> date_add('day', -value, date)", len(search)])
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(["'day'", '-' + search_el[1].strip(), 'date(' + search_el[0].strip() + ')']) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])
            q = re.sub(r'\bdate_sub\s*\(', r'date_add(', q)
            
            # trunc(str, pattern) -> date_format(date, pattern) + warning about different patterns
            search = regex.findall(r'\b(trunc)\s*(\(((?>[^()]++|(?2))*)\))*', q)
            replacements.append(['trunc(str, pattern) -> date_format(date, pattern)', len(search)])
            if len(search) > 0:
                print('Warning: There can be different date string patterns in Presto vs. Hive QL (patterns not translated here).')
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(['date(' + search_el[0].strip() + ')', search_el[1].strip()]) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])
            q = regex.sub(r'\btrunc\s*\(', r'date_format(', q)
            
        # Last, hive specific & vertica specific
        if dest == 'vertica':
            
            # unix_timestamp() -> extract(epoch from date)
            r = r'\bunix_timestamp\s*\(\s?'
            replacements.append(['unix_timestamp() -> extract(epoch from date)', len(re.findall(r, q))])
            q = re.sub(r, r'extract(epoch from ', q)
            
            # size() -> array_length()
            r = r'\bsize\s*\('
            replacements.append(['size() -> array_length()', len(re.findall(r, q))])
            q = re.sub(r, r'array_length(', q)
            
            # collect_list() -> listagg()
            # could use STRING_TO_ARRAY('['||col||']', ',' USING PARAMETERS max_length=1000000) to return an array type
            r = r'\bcollect_list\s*\('
            replacements.append(['collect_list() -> listagg()', len(re.findall(r, q))])
            q = re.sub(r, r'listagg(', q)
            
            # collect_set() -> listagg(distinct)
            # could use STRING_TO_ARRAY('['||col||']', ',' USING PARAMETERS max_length=1000000) to return an array type
            r = r'\bcollect_set\s*\('
            replacements.append(['collect_set() -> listagg(distinct)', len(re.findall(r, q))])
            q = re.sub(r, r'listagg(distinct ', q)
            
            # datediff -> timestampdiff + add unit + cast inside as date + cast output as date
            search = regex.findall(r'\b(datediff)\s*(\(((?>[^()]++|(?2))*)\))*', q)
            replacements.append(['datediff -> timestampdiff + add unit + cast inside and output as date', len(search)])
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(['date(' + s.strip() + ')' for s in search_el]) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])
            q = re.sub(r'\bdatediff\s*\(', r"timestampdiff('day', ", q)

            # date_add(str, value) -> date(timestampadd('day', value, date))
            search = regex.findall(r'\b(date_add)\s*(\(((?>[^()]++|(?2))*)\))*', q)
            replacements.append(["date_add(str, value) -> date(timestampadd('day', value, date))", len(search)])
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(["'day'", search_el[1].strip(), 'date(' + search_el[0].strip() + ')']) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])
            q = regex.sub(r'\b(date_add)\s*(\(((?>[^()]++|(?2))*)\))*', r'date(timestampadd\2)', q)

            # date_sub(str, value) -> date(timestampadd('day', -value, date))
            search = regex.findall(r'\b(date_sub)\s*(\(((?>[^()]++|(?2))*)\))*', q)
            replacements.append(["date_sub(str, value) -> date(timestampadd('day', -value, date))", len(search)])
            search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
            search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
            search_corrected = [', '.join(["'day'", '-' + search_el[1].strip(), 'date(' + search_el[0].strip() + ')']) for search_el in search_corrected]
            search_corrected = list(zip([s[2] for s in search], search_corrected))
            for s in search_corrected:
                q = q.replace(s[0], s[1])
            q = regex.sub(r'\b(date_sub)\s*(\(((?>[^()]++|(?2))*)\))*', r'date(timestampadd\2)', q)
                
    if src == 'presto':
        
        # First, presto specific & hive / vertica common
        
        # 1-indexing -> 0-indexing
        r = r'(?<=\[)(.+?)(?=\])'
        replacements.append(['1-indexing -> 0-indexing', len(re.findall(r, q))])
        q = re.sub(r, r'\1-1', q)
        
        # if needed, just signal that presto interval returns a date, not a timestamp
        if 'interval' in q:
            print("Warning: Note that in Presto, the INTERVAL operation returns a date, while in Hive and Vertica it returns a full timestamp (shouldn't be an issue).")
        
        # Then, presto specific & vertica specific
        if dest == 'vertica':
            
            # to_unixtime() -> extract(epoch from date)
            r = r'\bto_unixtime\s*\(\s?'
            replacements.append(['to_unixtime() -> extract(epoch from date)', len(re.findall(r, q))])
            q = re.sub(r, r'extract(epoch from ', q)
            
            # cardinality() -> array_length()
            r = r'\bcardinality\s*\('
            replacements.append(['cardinality() -> array_length()', len(re.findall(r, q))])
            q = re.sub(r, r'array_length(', q)
            
            # array_distinct(array_agg()) -> listagg(distinct)
            # could use STRING_TO_ARRAY('['||col||']', ',' USING PARAMETERS max_length=1000000) to return an array type
            r = r'\barray_distinct[\s\(]+array_agg\s*'
            replacements.append(['array_distinct(array_agg()) -> listagg(distinct)', len(re.findall(r, q))])
            q = re.sub(r, r'listagg(distinct ', q)
            # could we have more arguments than array_agg inside the array_distinct?
            # if so, then we're most probably in the standalone array_distinct case
            
            # array_agg() -> listagg()
            # could use STRING_TO_ARRAY('['||col||']', ',' USING PARAMETERS max_length=1000000) to return an array type
            r = r'\barray_agg\s*\('
            replacements.append(['array_agg() -> listagg()', len(re.findall(r, q))])
            q = re.sub(r, r'listagg(', q)
            
            # array_average() -> array_avg()
            r = r'\barray_average\s*\('
            replacements.append(['array_average() -> array_avg()', len(re.findall(r, q))])
            q = re.sub(r, r'array_avg(', q)
            
            # array_join() -> ||
            # more complex than expected
            
            # date_diff() -> datediff()
            r = r'\bdate_diff\s*\('
            replacements.append(['date_diff() -> datediff()', len(re.findall(r, q))])
            q = re.sub(r, r'datediff(', q)
            
            # date_add() -> date(timestampadd())
            r = r'(\bdate_add\s*)\s*(\(((?>[^()]++|(?2))*)\))*'
            replacements.append(['date_add() -> date(timestampadd())', len(re.findall(r, q))])
            q = regex.sub(r, r'date(timestampadd\2)', q)
            
        # Last, presto specific & hive specific
        if dest == 'hive':
            
            # to_unixtime() -> unix_timestamp()
            r = r'\bto_unixtime\s*\('
            replacements.append(['to_unixtime() -> unix_timestamp()', len(re.findall(r, q))])
            q = re.sub(r, r'unix_timestamp(', q)
            
            # cardinality() -> size()
            r = r'\bcardinality\s*\('
            replacements.append(['cardinality() -> size()', len(re.findall(r, q))])
            q = re.sub(r, r'size(', q)            
            
            # array_distinct(array_agg()) -> collect_list(distinct)
            r = r'\barray_distinct[\s\(]+array_agg\s*'
            replacements.append(['array_distinct(array_agg()) -> collect_list(distinct)', len(re.findall(r, q))])
            q = re.sub(r, r'collect_list(distinct ', q)
            # could we have more arguments than array_agg inside the array_distinct?
            # if so, then we're most probably in the standalone array_distinct case
            
            # array_agg() -> collect_list()
            r = r'\barray_agg\s*\('
            replacements.append(['array_agg() -> collect_list()', len(re.findall(r, q))])
            q = re.sub(r, r'collect_list(', q)

    if src == 'vertica':  
        
        # First, vertica specific & hive / presto common
        
        # ifnull -> coalesce
        r = r'\bifnull\s*\('
        replacements.append(['ifnull -> coalesce', len(re.findall(r, q))])
        q = re.sub(r, r'coalesce(', q)
        
        # zeroifnull(x) -> coalesce(x, 0)
        r = r'\bzeroifnull\b\s*\(([\w\s./\-\+\*]+|\w*\s*(\((?>[^()]++|(?2))*\)))\s*\)'
        subcounter = 0
        while len(regex.findall(r, q)) > 0:
            subcounter += len(regex.findall(r, q))
            q = regex.sub(r, r'coalesce(\1, 0)', q)
        replacements.append(['zeroifnull(x) -> coalesce(x, 0)', subcounter])
        
        # nullifzero(x) -> if(x = 0, null, x)
        r = r'\bnullifzero\b\s*\(([\w\s./\-\+\*]+|\w*\s*(\((?>[^()]++|(?2))*\)))\s*\)'
        subcounter = 0
        while len(regex.findall(r, q)) > 0:
            subcounter += len(regex.findall(r, q))
            q = regex.sub(r, r'if(\1 = 0, null, \1)', q)
        replacements.append(['nullifzero(x) -> if(x = 0, null, x)', subcounter])
        
        # bool -> boolean
        r = r'\bbool\b'
        replacements.append(['bool -> boolean', len(re.findall(r, q))])
        q = re.sub(r, r'boolean', q)
        
        # :: -> cast
        r = r'([\w\s./\-\+\*]+|\w*\s*(\((?>[^()]++|(?2))*\)))\s*::(\s*\w+)'
        subcounter = 0
        while len(regex.findall(r, q)) > 0:
            subcounter += len(regex.findall(r, q))
            q = regex.sub(r, r'cast(\1 as \3)', q)
        replacements.append([':: -> cast', subcounter])
        
        # to_timestamp() -> from_unixtime()
        r = r'\bto_timestamp\s*\('
        replacements.append(['to_timestamp() -> from_unixtime()', len(re.findall(r, q))])
        q = re.sub(r, r'from_unixtime(', q)
        
        # remove ilike and consequently insert lower()
        r = r"(\w+)\s*(\((?>[^()]++|(?2))*\))*\s+(ilike)"
        subcounter = 0
        while len(regex.findall(r, q)) > 0:
            subcounter += len(regex.findall(r, q))
            q = regex.sub(r, r"lower(\1\2) like", q)
        replacements.append(['remove ilike and consequently insert lower()', subcounter])
        
        # to_char -> date_format + warning that only works to cast dates as strings 
        # + warning about pattern letters differences
        r = r'\bto_char\s*\('
        replacements.append(['to_char() -> date_format()', len(re.findall(r, q))])
        if len(re.findall(r, q)) > 0:
            print("Warning: This function can only translate TO_CHAR when it's used to cast a date as a string.")
            print('Warning: Make sure you use the correct date patterns for your target language.')
        q = re.sub(r, r'date_format(', q)
        
        # Then, vertica specific & presto specific
        if dest == 'presto':
            
            # extract(epoch from date) -> to_unixtime()
            r = r"\bextract[\s\(]+epoch from\s+"
            replacements.append(['extract(epoch from date) -> to_unixtime()', len(re.findall(r, q))])
            q = re.sub(r, r"to_unixtime(", q)
            
            # array_length() -> cardinality()
            r = r'\barray_length\s*\('
            replacements.append(['array_length() -> cardinality()', len(re.findall(r, q))])
            q = re.sub(r, r'cardinality(', q)
            
            # listagg() -> array_join(array_agg())
            r = r'\blistagg\s*\('
            replacements.append(['listagg() -> array_join(array_agg())', len(re.findall(r, q))])
            q = re.sub(r, r'array_join(array_agg(', q)
            #print(    'Note that listagg returns a comma-separated list of strings.')
            
            # array_avg() -> array_average()
            r = r'\array_avg\s*\('
            replacements.append(['array_avg() -> array_average()', len(re.findall(r, q))])
            q = re.sub(r, r'array_average(', q)
            
            # concat() -> array_join()
            r = r"(concat)\s*(\(((?>[^()]++|(?2))*)\))"
            subcounter = 0
            while len(regex.findall(r, q)) > 0:
                subcounter += len(regex.findall(r, q))
                q = regex.sub(r, r"array_join(array[\3], ',')", q)
            replacements.append(['concat() -> array_join()', subcounter])
            
            # || -> array_join() (vertica to presto)
            # more complex than expected
            
            # datediff() or timestampdiff() -> date_diff()
            r = r'\b(datediff|timestampdiff)\s*\('
            replacements.append(['datediff() or timestampdiff() -> date_diff()', len(re.findall(r, q))])
            q = re.sub(r, r'date_diff(', q)
            
            # timestampadd() -> date_add()
            r = r'\btimestampadd\s*\('
            replacements.append(['timestampadd() -> date_add()', len(re.findall(r, q))])
            q = re.sub(r, r'date_add(', q)

            # timestamp_trunc() or trunc() -> date_format()
            r = r'\b(timestamp_trunc|trunc)\s*\('
            replacements.append(['timestamp_trunc() or trunc() -> date_format()', len(re.findall(r, q))])
            q = re.sub(r, r'date_format(', q)
            if len(re.findall(r, q)) > 0:
                print('Warning: Make sure you use the correct date patterns for your target language.')

            # date_part() -> date_trunc()
            r = r'\bdate_part\s*\('
            replacements.append(['date_part() -> date_trunc()', len(re.findall(r, q))])
            q = re.sub(r, r'date_trunc(', q)
            
        # Then, vertica specific & hive specific
        if dest == 'hive':
            
            # extract(epoch from date) -> unix_timestamp()
            r = r"\bextract[\s\(]+epoch from\s+"
            replacements.append(['extract(epoch from date) -> unix_timestamp()', len(re.findall(r, q))])
            q = re.sub(r, r"unix_timestamp(", q)
            
            # array_length() -> size()
            r = r'\barray_length\s*\('
            replacements.append(['array_length() -> size()', len(re.findall(r, q))])
            q = re.sub(r, r'size(', q)
            
            # listagg() -> collect_list()
            r = r'\listagg\s*\('
            replacements.append(['listagg() -> collect_list()', len(re.findall(r, q))])
            q = re.sub(r, r'collect_list(', q)
            print(    'Note that listagg returns a comma-separated list of strings, while collect_list in Hive returns an array.')
            
            # timestamp_trunc() -> trunc()
            r = r'\btimestamp_trunc\s*\('
            replacements.append(['timestamp_trunc() -> trunc()', len(re.findall(r, q))])
            q = re.sub(r, r'trunc(', q)
            
    # 2. To specific languages
    
    # presto / vertica common & hive specific
    if dest == 'hive':
        
        # index (this one was hard, it finds back the columns corresponding to the references in group by or order by)
        
        try:
            # First, get the column expressions
            # We want to get the list of column references only if there is a group by afterwards
            #columns_original = re.findall(r'(?<=\bselect\b)([\S\s]+?)(?=\bfrom\b)', q)
            columns_original = [re.split(r'\bfrom\b', cols)[0] for cols in re.split(r'\bselect\b', q) if re.search(r'\bgroup\b', cols) is not None]
            # Where to split column expressions? I can replace all commas inside functional expressions
            # or arrays with a ';', then split on the remaining commas, then replace back the ';' with commas.
            functions = [[''.join(f) for f in regex.findall(r'(\w+\s*)([\(\[](?>[^()]++|(?2))*[\)\]])+\s*', c)] for c in columns_original]
            for i in range(len(columns_original)):
                for f in functions[i]:
                    columns_original[i] = columns_original[i].replace(f, f.replace(',', ';'))
            columns_split = [[col.strip() for col in cols.split(',')] for cols in columns_original]
            columns_split = [[re.sub(r';', r',', col) for col in cols] for cols in columns_split]
            # For each column, remove 'as', and remove the last word
            # except if it's alone (no space) or if it includes closing parentheses or brackets 
            # (then it's part of the column expression and should be kept).
            columns_split = [[re.sub(r'\bas\s+?', r'', col) for col in cols] for cols in columns_split]
            columns_split = [[re.sub(r'\s+?[\w^\)^\]]+\s*$', r'', col) for col in cols] for cols in columns_split]

            # Then, get the group by and order by expressions. We use the same trick as before
            # to split them, except that we keep the spaces and newlines.
            groupby_original = regex.findall(r'(?<=group by)([\S\s]+?)(?=order\s+by|having|select|union|limit|$|\s+,\s+\w+\s+as)', q)
            groupby_split = deepcopy(groupby_original)
            functions = [[''.join(f) for f in regex.findall(r'(\w+\s*)([\(\[](?>[^()]++|(?2))*[\)\]])+\s*', c)] for c in groupby_split]
            for i in range(len(groupby_split)):
                for f in functions[i]:
                    groupby_split[i] = groupby_split[i].replace(f, f.replace(',', ';'))
            groupby_split = [cols.split(',') for cols in groupby_split]
            groupby_split = [[re.sub(r';', r',', col) for col in cols] for cols in groupby_split]

            orderby_original = regex.findall(r'(?<=order by)([\S\s]+?)(?=select|union|limit|$|\s+,\s+\w+\s+as)', q)
            orderby_split = deepcopy(orderby_original)
            functions = [[''.join(f) for f in regex.findall(r'(\w+\s*)([\(\[](?>[^()]++|(?2))*[\)\]])+\s*', c)] for c in orderby_split]
            for i in range(len(orderby_split)):
                for f in functions[i]:
                    orderby_split[i] = orderby_split[i].replace(f, f.replace(',', ';'))
            orderby_split = [cols.split(',') for cols in orderby_split]
            orderby_split = [[re.sub(r';', r',', col) for col in cols] for cols in orderby_split]

            # Remove the column references that have been commented out
            columns_split = [[t for t in cols if not (t.strip().startswith('/*') or t.strip().endswith('*/'))] for cols in columns_split]
            orderby_split = [[t for t in cols if not (t.strip().startswith('/*') or t.strip().endswith('*/'))] for cols in orderby_split]
            groupby_split = [[t for t in cols if not (t.strip().startswith('/*') or t.strip().endswith('*/'))] for cols in groupby_split]

            # Then, replace the column references by the column expression
            orderby_modified = []
            orderby_subcounter = 0
            for idx, search in enumerate(orderby_split):
                cols_modified = []
                for col in search:
                    # In these expressions we only need the column number, and potentially the asc / desc keyword.
                    # Therefore, we remove all space, remove asc / desc, potentially a last closing bracket,
                    # and if only a number remains, then it's a column reference, that we need to fetch back. 
                    col_index = re.sub(r'(\s|desc|asc|\)|\]|,)*', r'', col)
                    if col_index.isnumeric():
                        cols_modified.append(col.replace(col_index, columns_split[idx][int(col_index)-1]))
                        orderby_subcounter += 1
                    # Else, keep as it is (already valid in hive)
                    else:
                        cols_modified.append(col)
                orderby_modified.append(','.join(cols_modified))        
            groupby_modified = []
            groupby_subcounter = 0
            for idx, search in enumerate(groupby_split):
                cols_modified = []
                for col in search:
                    col_index = re.sub(r'(\s|\)|\]|,)*', r'', col)
                    if col_index.isnumeric():
                        cols_modified.append(col.replace(col_index, columns_split[idx][int(col_index)-1]))
                        groupby_subcounter += 1
                    else:
                        cols_modified.append(col)
                groupby_modified.append(','.join(cols_modified))

            # Finally, replace in original string
            for i in range(len(groupby_original)):
                q = q.replace(groupby_original[i], groupby_modified[i], 1)
                # count=1 to make just 1 replacement, in case there are several times the same group by in the query
            for i in range(len(orderby_original)):
                q = q.replace(orderby_original[i], orderby_modified[i], 1)
            replacements.append(['replace column positions in group by with column expressions', groupby_subcounter])
            replacements.append(['replace column positions in order by with column expressions', orderby_subcounter])
        except:
            # It doesn't work very well for nested queries -> in these cases, change session parameters
            q = '\nSET hive.groupby.orderby.position.alias=true;\n' + q
            replacements.append(['change hive session parameters to use column positions', 1])
            
        # unnest -> lateral view with re-aliasing
        r = r'cross\s+join\s+unnest\s*(\([\S\s]+\))\s+as\s+(\w+)\s+\((\w+)\)'
        if len(re.findall(r, q)) > 0:
            print("Warning: You cannot re-alias column names in Hive's LATERAL VIEW. Translation to Hive is only available for expressions such as CROSS JOIN UNNEST (original_column) AS new_column.")
        
        # unnest -> lateral view
        r = r'cross\s+join\s+unnest\s*(\([\S\s]+\))\s+as\s+(\w+)'
        replacements.append(['cross join unnest -> lateral view explode', len(re.findall(r, q))])
        q = re.sub(r, r'lateral view explode\1 t as \2', q)

        # mod -> pmod
        r = r'\bmod\s*\('
        replacements.append(['mod -> pmod', len(re.findall(r, q))])
        q = re.sub(r, r'pmod(', q)
        
        # varchar -> string, only when varchar length isn't specified
        r = r'\bvarchar(?!\s*\()'
        replacements.append(["varchar -> string, only when varchar length isn't specified", len(re.findall(r, q))])
        q = re.sub(r, r'string', q)
        
        # " -> `
        r = r'"'
        replacements.append(['" -> `', len(re.findall(r, q))])
        q = re.sub(r, r'`', q)
        
        # array[] -> array()        
        r = r"(\barray)\s*(\[([\S\s]*?)\])"
        subcounter = 0
        while len(re.findall(r, q)) > 0:
            subcounter += len(re.findall(r, q))
            q = re.sub(r, r'\1(\3)', q)
        replacements.append(['array[] -> array()', subcounter])
        
        # date -> to_date
        r = r'\bdate\s*\('
        replacements.append(['date() -> to_date()', len(re.findall(r, q))])
        q = re.sub(r, r'to_date(', q)
        
        # datediff() or date_diff() or timestampdiff() -> datediff() + remove unit + reverse output
        # split the expression(s) into parts
        search = regex.findall(r'\b(datediff|date_diff|timestampdiff)\s*(\(((?>[^()]++|(?2))*)\))*', q)
        replacements.append(['datediff() or date_diff() or timestampdiff() -> datediff() + remove unit + reverse output', len(search)])
        if len(search) > 0:
            print("Warning: Hive only supports 'day' difference between 2 dates.")
        # look for the inside of the brackets (2nd element) and remove the first element (the unit)
        # also keep the original and use this tuple (old, new) to run a replacement
        search = [(s[1], '(' + ','.join(s[1].split(',')[1:]).strip()) for s in search]
        for s in search:
            q = q.replace(s[0], s[1])
        q = re.sub(r'\b(datediff|date_diff|timestampdiff)\s*\(', r'-datediff(', q)
        
        # timestampadd or date_add(unit_str, value, date) -> date_add(date, value)
        search = re.findall(r"(\bdate_add\s*\('\w+',\s*|timestampadd\s*\('\w+',\s*)", q)
        replacements.append(['timestampadd or date_add(unit_str, value, date) -> date_add(date, value)', len(search)])
        # display warning if necessary (i.e. if other units than 'day' are used)
        if len(search) > 0:
            for s in search:
                s2 = re.findall(r"(\b(date_add|timestampadd)\s*\()'day',\s*", s)
                if len(s2) == 0:
                    print('Warning: In Hive, you can only add or remove days (no other units).')
                    break
        # start by removing the unit
        q = re.sub(r"(\b(date_add|timestampadd)\s*\()'\w+',\s*", r'\1', q)
        # then split elements and invert them
        search = regex.findall(r"\b(date_add|timestampadd)\s*(\(((?>[^()]++|(?2))*)\))*", q)
        search_corrected = [re.sub(r'\)\s*,', r');', s[2]) for s in search]
        search_corrected = [s.split(';') if ';' in s else s.split(',') for s in search_corrected]
        search_corrected = [', '.join([search_el[1].strip(), search_el[0].strip()]) for search_el in search_corrected]
        search_corrected = list(zip([s[2] for s in search], search_corrected))
        for s in search_corrected:
            q = q.replace(s[0], s[1])
        # finally rename the function
        q = re.sub(r'\btimestampadd\s*\(', r'date_add(', q)
        
        # date_part or date_trunc(part, date) -> extract(part from date) (or trunc(date, 'PART'))
        search = regex.findall(r'\b(date_part|date_trunc)\s*(\(((?>[^()]++|(?2))*)\))*', q)
        replacements.append(['date_part or date_trunc(part, date) -> extract(part from date)', len(search)])
        search_corrected = [s[2].split(',') for s in search]
        search_corrected = [[search_el[0], ','.join(search_el[1:])] for search_el in search_corrected]
        search_corrected = [' from '.join([search_el[0].strip(), search_el[1].strip()]) for search_el in search_corrected]
        search_corrected = list(zip([s[2] for s in search], search_corrected))
        for s in search_corrected:
            q = q.replace(s[0], s[1])
        q = re.sub(r'\b(date_part|date_trunc)\s*\(', r'extract(', q)
        
    # hive / vertica common & presto specific
    if dest == 'presto':
        
        # cast division as float
        r = r'/'
        replacements.append(['cast division as float', len(re.findall(r, q))])
        q = re.sub(r, r'*1.0000 /', q)
        # this actually isn't enough to cast one member of the division as double, but 4 decimals should be enough for most cases
        
        # 0-indexing -> 1-indexing
        r = r'(?<=\[)(.+?)(?=\])'
        replacements.append(['0-indexing -> 1-indexing', len(re.findall(r, q))])
        q = re.sub(r, r'\1+1', q)
    
        # add date() when interval is used
        r = r'''(=)([\S\s]+\binterval\b[\s'"\d]+[\w]+)'''
        replacements.append(['add date() when interval is used', len(re.findall(r, q))])
        q = re.sub(r, r'= date(\2)', q)
        
    # hive / presto common & vertica specific
    if dest == 'vertica':
        
        # from_unixtime() -> to_timestamp()
        r = r'\bfrom_unixtime\s*\('
        replacements.append(['from_unixtime() -> to_timestamp()', len(re.findall(r, q))])
        q = re.sub(r, r'to_timestamp(', q)
        
        # if -> case when
        # I split the members of the IF in order to change the syntax
        searched = regex.findall(r'(if\s*)(\(((?>[^()]++|(?2))*)\))', q)
        joined = [''.join(t[:2]) for t in searched]
        modified = [t[-1].split(',') for t in searched]
        modified = [f'case when {t[0]} then {t[1]} else {t[2]} end'.replace('  ', ' ') for t in modified]
        for i in range(len(joined)):
            q = q.replace(joined[i], modified[i])
        replacements.append(['if -> case when', len(searched)])
        
        # date_format() -> to_char() + cast as date + warning about pattern letters differences
        r = r'(\bdate_format\s*)\s*(\(((?>[^()]++|(?2))*)\))*'
        if len(re.findall(r, q)) > 0:
            print('Warning: Make sure you use the correct date patterns for your target language.')
        replacements.append(['date_format() -> to_char() + cast as date', len(re.findall(r, q))])
        q = re.sub(r, r'date(to_char\2)', q)
    
    # 3. Final results
    
    # Put back capital letters
    r = r'''(\b\w+\s*\(|\b(select|from|where|group by|order by|union|all|intersect|interval|left|right|inner|join|cross|unnest|lateral|view|explode|between|in|as|or|and|with|set|having|limit|outer|like|ilike|rlike|is|not|null|partition|by|over|on|case|when|then|else|end|preceding|following|date|timestamp|varchar|double|int|integer|string|bool|boolean|bigint|smallint|tinyint|float|insert|desc|asc|distinct)\b)'''
    q = re.sub(r, lambda m: f"{m.group(1).upper()}", q)
    
    # Replace back inline comments, at the correct position
    def replace_nth(s, old, new, n):
        where = [m.start() for m in re.finditer(old, s)][n-1]
        before = s[:where]
        after = s[where:]
        after = after.replace(old, new, 1)
        s = before + after
        return s
    for i in range(len(newlines)):
        q = replace_nth(q, newlines[i], newlines_and_comments[i], i+1)
    
    # Show results
    if verbose:
        # delete replacement information when 0 replacements
        replacements = [r for r in replacements if r[1] != 0]
        # print replacements in a nice format
        print(f'\n{sum([r[1] for r in replacements])} replacements in total:\n')
        [print(f'  • {r[0]}:  {r[1]}') for r in replacements]
    
    return print(q)