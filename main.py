from langchain_community.llms import Ollama
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.graphs import Neo4jGraph
import regex
import configparser
import json

config = configparser.ConfigParser()
config.read('config.ini')
NEO4J_URI = config['DEFAULT']['NEO4J_URI']
NEO4J_USER = config['DEFAULT']['NEO4J_USER']
NEO4J_PASS = config['DEFAULT']['NEO4J_PASS']

DEBUG = config.getboolean('DEFAULT', 'DEBUG')

MODEL = config['DEFAULT']['MODEL']
CONTEXT_WINDOW = int(config['DEFAULT']['MODEL_CONTEXT_WINDOW'])
INDEX = config['DEFAULT']['INDEX']

jargon_token_ratio_threshold = 0.5
semantic_search_num_to_get = 7
jargon_search_num_to_get = 7



def get_docs(embedding):
    vector_search_query = """
        WITH $embedding as e
        CALL db.index.vector.queryNodes($index, $k, e) YIELD node, score
        RETURN node.title AS result
    """
    context = graph.query(vector_search_query, {'embedding': embedding, 'k': semantic_search_num_to_get, 'index': INDEX})

    context = [x['result'] for x in context]

    if DEBUG:
        print(f"Semantic search results: {context}")

    return context

def get_jargon(query, embedding):

    # strip all non-alphanumeric
    pattern = regex.compile('[\\W_]+')
    query = pattern.sub(' ', query)
    pattern2 = regex.compile('[ ]+')
    query = pattern2.sub(' ', query)

    words = query.split(' ')

    jargon = []

    # jargon has a high number of tokens per word
    for word in words:
        num_tokens = llm.get_num_tokens(word)
        if len(word) >= 2 and num_tokens / len(word) > jargon_token_ratio_threshold:
            jargon.append(word)

    neo4j_query = """
        WITH $jargon AS jargon
        MATCH (n:Note)
        WHERE any(word IN jargon WHERE n.text CONTAINS word)

        WITH n, vector.similarity.cosine(n.embedding, $query) AS score

        RETURN n.title AS result, score
        ORDER BY score DESC
        LIMIT $k
    """

    results = graph.query(neo4j_query, {'jargon': jargon, 'query': embedding, 'k': jargon_search_num_to_get})
    results =  [x['result'] for x in results]

    if DEBUG:
        print(f"Jargon search results: {results}")
    return results

def get_related_documents(context):
    query = """
        WITH $context AS context
        MATCH (a:Note) -[:Link]-> (b:Note)
        WHERE any(title in context WHERE a.title = title)
        RETURN b.title AS title
    """
    results = graph.query(query, {'context': context})

    results = [x['title'] for x in results]

    context = context + results

    # remove duplicates
    context = list(dict.fromkeys(context))

    if DEBUG:
        print(f"Linked docs from Obsidian: {context}")
    return context

def get_context_text(context, available_tokens):
    context_obj = []

    query = """
        MATCH (a:Note)
        WHERE a.title = $title
        RETURN a.title as title, a.text as text
    """
    for note in context:
        results = graph.query(query, {'title': note})

        for result in results:
            #context_text += f"# {result['title']}\n"
            #context_text += result['text']
            #context_text += "\n\n"
            context_obj.append(result)

    context_tokens = llm.get_num_tokens(json.dumps(context_obj))
    
    if DEBUG:
        print(f"Context length: {len(json.dumps(context_obj))} characters; {context_tokens} tokens")
    
    trimmed = False
    if context_tokens > available_tokens:
        trimmed = True

    while context_tokens > available_tokens:
        if len(context_obj) == 1:
            max_length = round((len(context_obj[0]['text']) / context_tokens) * available_tokens)
            context_obj[0]['text'] = context_obj[0]['text'][:max_length]
            break
        else:
            context_obj.pop(-1)

        context_tokens = llm.get_num_tokens(json.dumps(context_obj))

    if trimmed and DEBUG:
        print(f"Context length: {len(json.dumps(context_obj))} characters; {context_tokens} tokens")
        print(f"Remaining Docs: {[x['title'] for x in context_obj]}")

    context = json.dumps(context_obj)

    return context

def semantic_sort(context, embedding):
    # remove duplicates
    context = list(dict.fromkeys(context))

    query = """
        WITH $context as context
        MATCH (n:Note)
        WHERE any(title in context WHERE n.title = title)
        WITH n, vector.similarity.cosine(n.embedding, $query) AS score

        RETURN n.title AS result, score
        ORDER BY score DESC
    """
    results = graph.query(query, {'context': context, 'query': embedding})

    return [x['result'] for x in results]

'''
' Give the model a list of document titles and the prompt, and ask which documents it wants
'''
def ask_jeeves(context, query, message_history):

    message_history_text = json.dumps(message_history)

    context_list = '\n'.join([f"* {doc}" for doc in context])

    json_pattern = regex.compile(r'\{(?:[^{}]|(?R))*\}')

    system_prompt = """
You are a content filter in a RAG pipeline for an assistant to a penetration tester conducting security assessments. Consider the following message history, and user query. You will be given a list of documents that are available to assist you while responding to the query.
Your task is to first repeat all of the available document titles, adding 'yes' or 'no' at the end of the line to note whether it would be of use when answering the user's query. Your response should then include a valid JSON object with the following structure containing the titles marked as useful:

{
    "context":[
        "document_1",
        "document_2",
       ...
    ]
}
"""

#    system_prompt = """
#You are a content filter in a RAG chain for an assistant for a penetration tester conducting security reviews.
#Consider the user's prompt, and the titles of documents that are available to be included as context for the query. All documents are related to security testing, or are background information on technologies. Note that documents contain very little overlap - if there are multiple documents in the same general area, they may all contain useful information. 
#Your task is to return a JSON object containing a list of document titles from the available documents, which are likely to contain information that would be useful when answering the user's query. There is no limit to the number of documents that can be included, however the most interesting documents should appear earlier in your response.
#Your response should only include a JSON object with the following structure:
#
#{{
#    "context":[
#        "document_1",
#        "document_2",
#        ...
#    ]
#}}
#"""

    prompt = f"""
------- SYSTEM MESSAGE ------
{system_prompt}
-------- END SYSTEM MESSAGE -----

-------- PREVIOUS MESSAGES ------
{message_history_text}
-------- END PREVIOUS MESSAGES -----

-------- AVAILABLE DOCUMENTS ----
{context_list}
-------- END AVAILABLE DOCUMENTS -----

-------- USER QUERY -------------
{query}
-------- END USER QUERY ---------
"""

    if DEBUG:
        print("LLM RAG filter prompt:")
        print(prompt)

    num_tries = 3
    new_context = []

    for i in range(num_tries):
        response = llm.invoke(prompt)
        response_json = json_pattern.findall(response)
        for j in response_json:
            try:
                response_obj = json.loads(j)
                new_context = new_context + response_obj['context']
            except (ValueError, KeyError) as e:
                response_obj = {'context': []}
                new_context = new_context + response_obj['context']
                if DEBUG:
                    print(f"FAILED TO PARSE LLM RESPONSE: {j}")
            if DEBUG:
                print(f"LLM Response {i}: {response}")
                print()
                print(f"LLM choices attempt {i}: {response_obj}")
                print()

    if len(new_context) > 0:
        # remove duplicates
        new_context = list(dict.fromkeys(new_context))

    if DEBUG:
        print(f"Documents chosen by LLM: {new_context}")
    return new_context

def summarise_history(message_history, query):
    history_text = json.dumps(message_history)

    system_prompt = """
You are a part of a RAG pipeline for an assistant for a penetration tester. You will be given a message history of the conversation between the assistant and the user so far, as well as the user's current query.
Your task is to concisely summarise the conversation history, as well as the current user query, in order for the rest of the RAG pipeline to know which documents to fetch and include in the context.
Ensure that critical keywords and information about the discussion is used in your response. Pay particular attention to recent messages and the current user query.
If there has been a substantial change in topic over the conversation, only summarise the current topic.
Do not comment on your task. Do not attempt to answer the query. Your output should only be the message summary.
"""

    prompt = f"""
------ SYSTEM MESSAGE -------
{system_prompt}
------ END SYSTEM MESSAGE -------

------ MESSAGE HISTORY --------
{history_text}
------ END MESSAGE HISTORY --------

------ USER QUERY ---------
{query}
------ END USER QUERY -------
"""
    response = llm.invoke(prompt)

    if DEBUG:
        print(f"Summarised message history: {response}")

    return response



def rag(query, message_history, available_tokens):
    if len(message_history) > 0:
        query = summarise_history(message_history, query)

    # embed the query for semantic search
    embedding = emb.embed_query(query)
    # get simple semantic search results
    context = get_docs(embedding)
    # add in jargon search results
    context = context + get_jargon(query, embedding)
    # get the related documents for the current searches
    context = get_related_documents(context)

    # remove duplicates and sort semantically
    context = semantic_sort(context, embedding)
    
    # ask the LLM which documents would be useful to filter out rubbish
    context = ask_jeeves(context, query, message_history)

    # retrieve the actual text of the context
    context = get_context_text(context, available_tokens)

    return context

def handle_user_message(query, history):

    history_text = json.dumps(history)
    tokens_for_history = llm.get_num_tokens(history_text)

    system_prompt = f"""
You are an assistant to a penetration tester performing security reviews. Consider the following query from a user, as well as the associated context information, which has been provided to aid in your response.
If the information requested by the user is not included in the context, then say that you do not have the information. Do not attempt to answer queries that were not included in the context.
Content in the context can be treated as reliable; code in the context is verified to work. If you can reuse scripts verbatim, then do so. If they need to be tweaked to achieve the desired results, then make the necessary changes.
"""
    tokens_for_system_prompt = llm.get_num_tokens(system_prompt)

    used_tokens = tokens_for_history + tokens_for_system_prompt + 200 # add a few tokens in for the prompt formatting

    available_tokens = CONTEXT_WINDOW - used_tokens

    context = rag(query, message_history, available_tokens)

    prompt = f"""
------- SYSTEM MESSAGE -------
{system_prompt}
------- END SYSTEM MESSAGE ------

------- PREVIOUS MESSAGES -------
{history_text}
------- PREVIOUS MESSAGES -------

------- CONTEXT INFORMATION --------
{context}
------- END CONTEXT INFORMATION -------

------- USER QUERY --------
{query}
------- END USER QUERY -------
"""

    return llm.invoke(prompt)


if __name__ == '__main__':
    graph = Neo4jGraph(
        url=NEO4J_URI,
        username=NEO4J_USER,
        password=NEO4J_PASS
    )

    llm = Ollama(
            model=MODEL,
            num_ctx=CONTEXT_WINDOW
        )
    emb = OllamaEmbeddings(model=MODEL)

    message_history = []

    while True:
        query = input("> ")
        print()
        response = handle_user_message(query, message_history)
        print(response)
        message_history.append({
            'role': 'user',
            'message': query
        })
        message_history.append({
            'role': 'assistant',
            'message': response
        })
