# 1.Read incoming customer emails
# 2.Classify them by urgency and topic
# 3.Search relevant documentation to answer question
# 4.Draft appropriate responses
# 5.Escalate complex issues to human agents
# 6.Schedule follow-ups when needed


# 1.设计state
from typing import TypedDict, Literal
from langgraph.graph import MessagesState, START, END, StateGraph
from langgraph.types import Command, interrupt, RetryPolicy
from pydantic import BaseModel, Field

from QQEmailListener import QQEmailListener
import smtplib
from email.mime.text import MIMEText
from email.header import Header
import httpx

from langgraph.runtime import get_runtime

# 添加BeautifulSoup和re导入
from bs4 import BeautifulSoup
import re
import os

from feishu_api.feishu import FeishuAPI


class EmailClassification(TypedDict):
    intent: Literal['question', 'bug', 'building', 'feature', 'complex_request']
    urgency: Literal['low', 'medium', 'high', 'critical']
    terminal: Literal['Web', 'Windows', 'Android', 'Mac', 'iOS', 'Not provided']
    topic: str
    summary: str


class EmailAgentState(MessagesState):
    email_content: str
    sender_email: str
    email_id: str

    classification: EmailClassification | None

    handle_results: list[str] | None
    customer_history: dict | None

    draft_response: str | None


class QQEmail(TypedDict):
    sender: str
    password: str
    # receiver:str


class SearchAPIError(Exception):
    """搜索API相关异常"""
    pass


feishu = FeishuAPI(app_id=os.getenv('APP_ID'), app_secret=os.getenv('APP_SECRET'), app_token=os.getenv('APP_TOKEN'))

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

model = os.getenv('MODEL')
base_url = os.getenv('BASE_URL')
api_key = os.getenv('API_KEY')

receive_id=os.getenv('RECEIVE_ID')
table_id=os.getenv('TABLE_ID')

model = ChatOpenAI(
    model=model,
    base_url=base_url,
    api_key=api_key
)

classification_model = model.with_structured_output(EmailClassification)


def classify_intent(state: EmailAgentState) -> Command[
    Literal["search_documentation", "to_human", "draft_response", "bug_tracking"]]:
    print('开始分类邮件')

    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])

    classification_prompt = f"""
    Analyze this customer email and classify it:
    
    Email:{cleaned_email_content}
    From:{state['sender_email']}
    
    Provide classification including intent,urgency,topic,and summary
    and return json format 
    intent:Literal['question','bug','building','feature','complex_request']
    urgency:Literal['low','medium','high','critical']
    terminal:Literal['Web','Windows','Android','Mac','iOS','Not provided']
    """

    classification = classification_model.invoke(classification_prompt)

    print(f'分类完成：{classification}')
    intent = classification['intent']
    urgency = classification['urgency']

    if intent not in ['question', 'bug', 'building', 'feature', 'complex_request']:
        raise ValueError
    if urgency not in ['low', 'medium', 'high', 'critical']:
        raise ValueError
    if classification['terminal'] not in ['Web', 'Windows', 'Android', 'Mac', 'iOS', 'Not provided']:
        raise ValueError

    if intent in ['billing', 'complex_request'] or urgency in ['critical', 'high']:
        goto = 'to_human'
    elif intent in ['question', 'feature']:
        goto = 'search_documentation'
    elif intent == 'bug':
        goto = 'bug_tracking'
    else:
        goto = 'draft_response'

    print(f'进入 {goto}')
    return Command(
        update={
            'classification': classification,
            'email_content': cleaned_email_content
        },
        goto=goto
    )


def search_documentation(state: EmailAgentState) -> Command['draft_response']:
    print('开始搜索文档')

    classification = state.get('classification', {})
    query = f'{classification.get("intent", "")} {classification.get("topic", "")}'

    try:
        search_result = [
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
        update={'handle_results': search_result},
        goto='draft_response'
    )


def bug_tracking(state: EmailAgentState) -> Command['draft_response']:
    classification = state['classification']
    urgency = classification['urgency']
    if urgency in ['critical', 'high']:
        priority = 'P0'
    elif urgency == 'medium':
        priority = 'P1'
    else:
        priority = 'P2'
    print('正在提交bug')

    bug_description = state['email_content']

    submitter = state['sender_email']

    terminal = classification['terminal']
    feishu.to_feishu(table_id=table_id, data={
        "Bug 描述": bug_description,
        "提交人": submitter,
        "终端": terminal,
        "优先级": priority
    })

    print('Bug 已提交')

    return Command(
        update={'handle_results': f'Bug ticket created'},
        goto='draft_response'
    )


def to_human(state: EmailAgentState) -> Command[Literal['send_reply', END]]:
    # classification = state.get('classification', {})
    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])

    feishu.send_message(receive_id, cleaned_email_content)

    return Command(update={'handle_results': "已经将此紧急问题发送给专业团队，静待即可"}, goto='draft_response')


def draft_response(state: EmailAgentState) -> Command[Literal['send_reply']]:
    print('开始拟写回信')

    # 清理邮件内容中的HTML标签
    cleaned_email_content = clean_html_content(state['email_content'])

    classification = state.get('classification', {})
    context_sections = []

    if state.get('handle_results', None):
        formatted_docs = '\n'.join([f'- {doc}' for doc in state["handle_results"]])
        context_sections.append(f'Relevant documentation:\n{formatted_docs}')

    draft_prompt = f"""
    Draft a response to this email:
    {cleaned_email_content}
    
    Email intent: {classification.get('intent', 'unknown')}
    Urgency level: {classification.get('urgency', 'medium')}
    
    searched_documentation: {context_sections}
    
    Guidelines:
    - Be professional and helpful
    - Address their specific concern
    - Use the provided documentation when relevant
    """

    # print(draft_prompt)

    response = model.invoke(draft_prompt)

    print('拟写完成，准备发送')

    return Command(
        update={'draft_response': response.content},
        goto='send_reply'
    )


def send_reply(state: EmailAgentState) -> dict:
    subject = f"reply about {state['classification'].get('topic', '')}"
    body = state['draft_response']

    my_email = get_runtime(QQEmail).context['sender']

    msg = MIMEText(body, 'plain', 'utf-8')
    msg["Subject"] = Header(subject, 'utf-8')
    msg['From'] = my_email
    msg["To"] = state['sender_email']

    smtp_server = 'smtp.qq.com'
    smtp_port = 587
    sender_email = my_email
    password = get_runtime(QQEmail).context['password']
    server = None

    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, [msg["To"]], msg.as_string())
        print('邮件已发送')
    except smtplib.SMTPException as e:
        print('邮件发送失败：', str(e))
    finally:
        if server:
            server.close()

    return {}


workflow = StateGraph(EmailAgentState)

workflow.add_node(classify_intent, retry_policy=RetryPolicy(max_attempts=3))

workflow.add_node(search_documentation, retry_policy=RetryPolicy(max_attempts=3))

workflow.add_node(bug_tracking).add_node(to_human).add_node(draft_response).add_node(send_reply)

workflow.add_edge(START, 'classify_intent')
workflow.add_edge('draft_response', 'send_reply')
workflow.add_edge('send_reply', END)

app = workflow.compile()


def main(test: bool = True):
    """The default is test mode. If you need to launch formally, pass in test=False."""
    email_address = os.getenv('QQEMAIL')
    password = os.getenv('EMAIL_PASSWORD')  # QQ邮箱需要使用授权码而非密码

    if not email_address or not password:
        print("请设置环境变量 QQEMAIL 和 EMAIL_PASSWORD")

    listener = QQEmailListener(email_address, password)

    if not test:
        while True:
            try:
                email = next(listener.listen_for_emails(check_interval=5))
                if email:
                    app.invoke({
                        'email_content': email['email_content'],
                        'sender_email': email['sender_email'],
                        'email_id': email['email_id']
                    },
                        context=QQEmail(sender=email_address, password=password)
                    )
            except:
                continue


if __name__ == '__main__':
    main(False)
