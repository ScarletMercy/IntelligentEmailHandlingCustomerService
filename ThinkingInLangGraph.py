# 1.Read incoming customer emails
# 2.Classify them by urgency and topic
# 3.Search relevant documentation to answer question
# 4.Draft appropriate responses
# 5.Escalate complex issues to human agents
# 6.Schedule follow-ups when needed


# 1.设计state
from typing import TypedDict,Literal
from langgraph.graph import MessagesState, START, END, StateGraph
from langgraph.types import Command, interrupt, RetryPolicy
from pydantic import BaseModel

from QQEmailListener import QQEmailListener
import smtplib
from email.mime.text import MIMEText
from email.header import Header

from langgraph.runtime import get_runtime

# 添加BeautifulSoup和re导入
from bs4 import BeautifulSoup
import re

class EmailClassification(TypedDict):
    intent:Literal['question','bug','building','feature','complex_request']
    urgency:Literal['low','medium','high','critical']
    topic:str
    summary:str

class EmailAgentState(MessagesState):
    email_content:str
    sender_email:str
    email_id:str

    classification:EmailClassification|None

    search_results:list[str]|None
    customer_history:dict|None

    draft_response:str|None

class QQEmail(TypedDict):
    sender:str
    password:str
    # receiver:str

class SearchAPIError(Exception):
    """搜索API相关异常"""
    pass

# 添加清理HTML内容的函数
def clean_html_content(html_content) -> str:
    """
    使用BeautifulSoup清除HTML标签，并结合正则表达式提取正文内容
    """
    # 确保输入是字符串类型
    if not isinstance(html_content, str):
        html_content = str(html_content)
    
    # 如果内容为空，直接返回
    if not html_content.strip():
        return ""
    
    try:
        # 使用BeautifulSoup解析HTML并提取文本
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 移除script和style标签及其内容
        for script in soup(["script", "style"]):
            script.decompose()
        
        # 获取纯文本
        text = soup.get_text()
        
        # 使用正则表达式进一步清理文本
        # 将多个连续的空白字符替换为单个空格
        text = re.sub(r'\s+', ' ', text)
        
        # 去除首尾空白字符
        text = text.strip()
        
        return text
    except Exception as e:
        # 如果解析失败，返回原始内容（清理后的）
        print(f"HTML解析失败: {e}")
        # 简单地移除HTML标签
        clean_text = re.sub(r'<[^>]+>', '', html_content)
        clean_text = re.sub(r'\s+', ' ', clean_text)
        return clean_text.strip()

# 2.build node
from langchain_openai import ChatOpenAI
import os

model=os.getenv('MODEL')
base_url=os.getenv('BASE_URL')
api_key=os.getenv('API_KEY')


model=ChatOpenAI(
    model=model,
    base_url=base_url,
    api_key=api_key
)

classification_model=model.with_structured_output(EmailClassification)

# def read_email(state:EmailAgentState):
#     ...

def classify_intent(state:EmailAgentState)->Command[Literal["search_documentation", "human_review", "draft_response", "bug_tracking"]]:
    print('开始分类邮件')
    
    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])
    
    classification_prompt =f"""
    Analyze this customer email and classify it:
    
    Email:{cleaned_email_content}
    From:{state['sender_email']}
    
    Provide classification including intent,urgency,topic,and summary
    and return json format 
    intent:Literal['question','bug','building','feature','complex_request']
    urgency:Literal['low','medium','high','critical']
    """

    classification=classification_model.invoke(classification_prompt)

    print(f'分类完成：{classification}')

    if classification['intent'] in ['billing','complex_request'] or classification['urgency'] in ['critical','high']:
        goto='human_review'
    elif classification['intent'] in ['question','feature']:
        goto='search_documentation'
    elif classification['intent'] == 'bug':
        goto='bug_tracking'
    else:
        goto='draft_response'



    print(f'进入 {goto}')
    return Command(
        update={'classification':classification},
        goto=goto
    )
    # return goto


def search_documentation(state:EmailAgentState)->Command['draft_response']:

    print('开始搜索文档')

    classification=state.get('classification',{})
    query=f'{classification.get("intent","")} {classification.get("topic","")}'

    try:
        search_result=[
            "Reset password via Settings > Security > Change Password",
            "Password must be at least 12 characters",
            "Include uppercase, lowercase, numbers, and symbols"
        ]
        print('搜索完成')

    except SearchAPIError:
        # 处理搜索API错误
        search_result = ["暂时无法获取相关文档，请稍后再试。"]

    # 更新状态并返回命令
    return Command(
        update={'search_results': search_result},
        goto='draft_response'
    )


def bug_tracking(state:EmailAgentState)->Command['draft_response']:
    print('正在提交bug')
    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])
    ticket_id = 1

    # with open('bug_list.txt', 'r') as f:
    #
    #     for _ in f.readlines():  # 逐行迭代（不加载整个文件）
    #         print(_)
    #         ticket_id += 1

    with open('bug_list.txt','r+') as f:
        for _ in f.readlines():  # 逐行迭代（不加载整个文件）
            ticket_id += 1

        f.write(f"ticket{ticket_id}:{cleaned_email_content}\n")

        print('Bug 已提交')

    return Command(
        update={
            'search_results':[f'Bug ticket{ticket_id} created'],
        },
        goto='draft_response'
    )


def draft_response(state:EmailAgentState)->Command[Literal['send_reply']]:

    print('开始拟写回信')
    
    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])

    classification=state.get('classification',{})
    context_sections=[]

    if state.get('search_results',None):
        formatted_docs='\n'.join([f'- {doc}' for doc in state["search_results"]])
        context_sections.append(f'Relevant documentation:\n{formatted_docs}')

    draft_prompt=f"""
    Draft a response to this email:
    {cleaned_email_content}
    
    Email intent: {classification.get('intent','unknown')}
    Urgency level: {classification.get('urgency','medium')}
    
    searched_documentation: {context_sections}
    
    Guidelines:
    - Be professional and helpful
    - Address their specific concern
    - Use the provided documentation when relevant
    """

    # print(draft_prompt)

    response=model.invoke(draft_prompt)

    print('拟写完成，准备发送')

    return Command(
        update={'draft_response':response.content},
        goto='send_reply'
    )

def human_review(state:EmailAgentState)->Command[Literal['send_reply',END]]:
    classification=state.get('classification',{})
    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])

    human_decision=interrupt(
        {
            'email_id':state['email_id'],
            'original_email':cleaned_email_content,
            'draft_response':state.get('draft_response',input('发送内容：')),
            'urgency':classification.get('urgency'),
            'intent':classification.get('intent'),
            'action':'Please review and approve/edit this response'
        }
    )

    if human_decision.get('approved'):
        return Command(
            update={'draft_response':human_decision.get('edited_response',state['draft_response'])},
            goto='send_reply'
        )
    else:
        return Command(update={},goto=END)


def send_reply(state:EmailAgentState)->dict:
    subject=f"reply about {state['classification'].get('topic','')}"
    body=state['draft_response']

    my_email=get_runtime(QQEmail).context['sender']

    msg=MIMEText(body,'plain','utf-8')
    msg["Subject"]=Header(subject,'utf-8')
    msg['From']=my_email
    msg["To"]=state['sender_email']

    smtp_server='smtp.qq.com'
    smtp_port=587
    sender_email=my_email
    password=get_runtime(QQEmail).context['password']
    server=None

    try:
        server=smtplib.SMTP(smtp_server,smtp_port)
        server.starttls()
        server.login(sender_email,password)
        server.sendmail(sender_email,[msg["To"]],msg.as_string())
        print('邮件已发送')
    except smtplib.SMTPException as e:
        print('邮件发送失败：',str(e))
    finally:
        if server:
            server.close()


    return {}


workflow=StateGraph(EmailAgentState)

workflow.add_node(classify_intent)

workflow.add_node(search_documentation,retry_policy=RetryPolicy(max_attempts=3))

workflow.add_node(bug_tracking).add_node(human_review).add_node(draft_response).add_node(send_reply)

workflow.add_edge(START,'classify_intent')
# workflow.add_conditional_edges(START,classify_intent,["search_documentation", "human_review", "draft_response", "bug_tracking"])
workflow.add_edge('search_documentation','draft_response')
workflow.add_edge('bug_tracking','draft_response')
workflow.add_edge('human_review','draft_response')
workflow.add_edge('draft_response','send_reply')
workflow.add_edge('send_reply',END)

app=workflow.compile()



if __name__=='__main__':
    email_address = os.getenv('QQEMAIL')
    password = os.getenv('EMAIL_PASSWORD')  # QQ邮箱需要使用授权码而非密码

    if not email_address or not password:
        print("请设置环境变量 QQEMAIL 和 EMAIL_PASSWORD")

    listener = QQEmailListener(email_address, password)

    while True:
        email=next(listener.listen_for_emails(check_interval=5))
        if email:
            app.invoke({
                'email_content': email['email_content'],
                'sender_email': email['sender_email'],
                'email_id': email['email_id']
                 },
                context=QQEmail(sender=email_address,password=password)
            )



# 3.handle error





