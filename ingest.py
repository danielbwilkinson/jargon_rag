from langchain_community.llms import Ollama
from langchain_community.embeddings import OllamaEmbeddings
from neo4j import GraphDatabase
from os import listdir
from os.path import isfile, join
import re
import configparser

config = configparser.ConfigParser()
config.read('config.ini')
NEO4J_URI = config['DEFAULT']['NEO4J_URI']
NEO4J_USER = config['DEFAULT']['NEO4J_USER']
NEO4J_PASS = config['DEFAULT']['NEO4J_PASS']
model = config['DEFAULT']['MODEL']
index = config['DEFAULT']['INDEX']


llm = Ollama(model=model)
emb = OllamaEmbeddings(model=model)
gdb = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS))

obsidian_root_dir = config['DEFAULT']['OBSIDIAN_ROOT_DIR']
note_types = [
        {
            'name': 'primary',
            'directory': obsidian_root_dir + '01 - Primary Categories/'
        },
        {
            'name': 'secondary',
            'directory': obsidian_root_dir + '02 - Secondary Categories/'
        },
        {
            'name': 'content',
            'directory': obsidian_root_dir + '03 - Content/'
        }
]

def nuke_existing(gdb: GraphDatabase):
    gdb.execute_query("MATCH (:Note)-[a:Link]->(:Note) DELETE a")
    gdb.execute_query("MATCH (n:Note) DELETE n")
    gdb.execute_query("DROP INDEX $index", index=index)
    print("Deleted everything")


def create_index(gdb: GraphDatabase):

    embedding_dimension = len(emb.embed_query('aaa'))
    print(f"Embedding Dimension: {embedding_dimension}")

    # Create the vector index
    gdb.execute_query("""CREATE VECTOR INDEX $index_name IF NOT EXISTS
            FOR (n: Note) ON (n.embedding)
            OPTIONS {indexConfig: {
                `vector.dimensions`: $embedding_dim,
                `vector.similarity_function`: 'cosine'
            }}""", index_name=index, embedding_dim = embedding_dimension)

    print(f"Created vector index '{index}'")

def create_nodes(gdb: GraphDatabase):
    for note_type in note_types:

        files = [f for f in listdir(note_type['directory']) if isfile(join(note_type['directory'], f))]

        # first things first, create each node
        for file in files:
            filepath = join(note_type['directory'], file)
            with open(filepath) as f:
                content = f.read()

            title = file.split('.md')[0]
            
            upload_file(title, content, note_type['name'])



def upload_file(title, text, note_type):

    # parse the search tags, if there are any
    search_tags = re.search("^Search Tags:.*$", text)
    if search_tags:
        search_tags = search_tags[0].split('#')
        if len(search_tags) > 1:
            search_tags = search_tags[1:]
            search_tags = [x.strip() for x in search_tags]        
        else:
            search_tags = []
    else:
        search_tags = []

    # clean the text a bit so that it's more useful for the model.
    # if we always have "Primary Category", "Secondary Category", "Search Tags", etc at the top,
    # then the document embeddings will all end up more similar than they should.
    # also, during RAG, the model isn't getting the note titles, so add them in.

    cleaned_text = re.sub(r'^Primary Categories:.*\n?', '', text, flags=re.MULTILINE)
    cleaned_text = re.sub(r'^Secondary Categories:.*\n?', '', cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r'^Search Tags:.*\n?', '', cleaned_text, flags=re.MULTILINE)
    cleaned_text = f"# {title}\n" + cleaned_text
    
    embedding = emb.embed_documents([
        cleaned_text
    ])[0]

    query="""
        CREATE (note:Note { 
            title: $title,
            note_type: $note_type,
            original_text: $original_text,
            text: $cleaned_text,
            search_tags: $search_tags,
            embedding: $embedding
        })"""
    gdb.execute_query(query, title=title, note_type=note_type, cleaned_text=cleaned_text, original_text=text, search_tags=search_tags, embedding=embedding)
    print(f"Uploaded - Title: {title}, note_type: {note_type}")

def create_links(gdb: GraphDatabase):
    query="""
        MATCH (n:Note) RETURN n.title AS title
    """
    result = gdb.execute_query(query)
    titles = [x['title'] for x in result[0]]

    for title in titles:
        # get the original text (before we delete the primary and secondary category links)
        query = "MATCH (n:Note) WHERE n.title = $title RETURN n.original_text as text"
        result = gdb.execute_query(query, title=title)
        text = result[0][0]['text']
    
        links = parse_links(text)

        for link in links:
            query = """
            MATCH (src:Note), (dst:Note) WHERE src.title = $title AND dst.title = $link
            CREATE (src)-[:Link]->(dst)
            """
            gdb.execute_query(query, title=title, link=link)

def parse_links(text):
    link_re = re.compile('\\[\\[.*?\\]\\]')
    code_re = re.compile('(```.*?```|~~~.*?~~~)', re.DOTALL)
    image_re = re.compile('!\\[\\[.*?\\]\\]')

    text = code_re.sub('', text)
    text = image_re.sub('', text)
    links = link_re.findall(text)

    links = [re.sub('(\\[\\[|\\]\\]|\\\\)', '', link) for link in links]
    links = [link.split('|')[0].split('#')[0] for link in links]
    links = list(filter(None, links))
    
    return links

if __name__ == '__main__':
    nuke = input("Drop all data from neo4j before ingesting [y/N]? ")
    if nuke == 'y' or nuke == 'Y':
        nuke_existing(gdb)
    create_index(gdb)
    create_nodes(gdb)
    create_links(gdb)
