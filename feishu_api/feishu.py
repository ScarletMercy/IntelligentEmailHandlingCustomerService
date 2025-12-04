import json
import uuid

import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *
from lark_oapi.api.im.v1 import *


class FeishuAPI:
    def __init__(self,app_id, app_secret,app_token):
        self.__app_id=app_id
        self.__app_secret=app_secret
        self.__app_token=app_token

    def get_feishu(self,table_id, view_id):
        # 创建client
        client = lark.Client.builder() \
            .app_id(self.__app_id) \
            .app_secret(self.__app_secret) \
            .log_level(lark.LogLevel.DEBUG) \
            .build()

        # 构造请求对象
        request: SearchAppTableRecordRequest = SearchAppTableRecordRequest.builder() \
            .app_token(self.__app_token) \
            .table_id(table_id) \
            .user_id_type("user_id") \
            .page_size(20) \
            .request_body(SearchAppTableRecordRequestBody.builder()
                          .view_id(view_id)
                          .field_names(["ID", "价格", "商品信息"])
                          .sort([Sort.builder()
                                .field_name("价格")
                                .desc(False)
                                .build()
                                 ])
                          .automatic_fields(False)
                          .build()) \
            .build()

        # 发起请求
        response: SearchAppTableRecordResponse = client.bitable.v1.app_table_record.search(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.bitable.v1.app_table_record.search failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        # 处理业务结果
        # lark.logger.info(lark.JSON.marshal(response.data, indent=4))
        str_result = lark.JSON.marshal(response.data, indent=4)
        json_result = json.loads(str_result)
        return json_result

    def to_feishu(self,table_id, data: dict):
        # 创建client
        client = lark.Client.builder() \
            .app_id(self.__app_id) \
            .app_secret(self.__app_secret) \
            .log_level(lark.LogLevel.DEBUG) \
            .build()

        # 构造请求对象
        request: CreateAppTableRecordRequest = CreateAppTableRecordRequest.builder() \
            .app_token(self. __app_token) \
            .table_id(table_id) \
            .request_body(AppTableRecord.builder()
                          .fields(data)
                          .build()) \
            .build()

        # 发起请求
        response: CreateAppTableRecordResponse = client.bitable.v1.app_table_record.create(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.bitable.v1.app_table_record.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        # 处理业务结果
        lark.logger.info(lark.JSON.marshal(response.data, indent=4))

    def merge_feishu(self,table_id, record_id, data: dict):
        # 创建client
        client = lark.Client.builder() \
            .app_id(self. __app_id) \
            .app_secret(self. __app_secret) \
            .log_level(lark.LogLevel.DEBUG) \
            .build()

        # 构造请求对象
        request: UpdateAppTableRecordRequest = UpdateAppTableRecordRequest.builder() \
            .app_token(self. __app_token) \
            .table_id(table_id) \
            .record_id(record_id) \
            .request_body(AppTableRecord.builder() \
                          .fields(data)
                          .build()) \
            .build()

        # 发起请求
        response: UpdateAppTableRecordResponse = client.bitable.v1.app_table_record.update(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.bitable.v1.app_table_record.update failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        # 处理业务结果
        lark.logger.info(lark.JSON.marshal(response.data, indent=4))


    # SDK 使用说明: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/server-side-sdk/python--sdk/preparations-before-development
    # 以下示例代码默认根据文档示例值填充，如果存在代码问题，请在 API 调试台填上相关必要参数后再复制代码使用
    # 复制该 Demo 后, 需要将 "YOUR_APP_ID", "YOUR_APP_SECRET" 替换为自己应用的 APP_ID, APP_SECRET.
    def send_message(self,receive_id,content):

        content='{"text":'+f'"{content}"'+'}'

        # 创建client
        client = lark.Client.builder() \
            .app_id(self.__app_id) \
            .app_secret(self.__app_secret) \
            .log_level(lark.LogLevel.DEBUG) \
            .build()

        # 构造请求对象
        request: CreateMessageRequest = CreateMessageRequest.builder() \
            .receive_id_type("open_id") \
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(receive_id)
                          .msg_type("text")
                          .content(content)
                          .uuid(str(uuid.uuid4()))
                          .build()) \
            .build()

        # 发起请求
        response: CreateMessageResponse = client.im.v1.message.create(request)

        # 处理失败返回
        if not response.success():
            lark.logger.error(
                f"client.im.v1.message.create failed, code: {response.code}, msg: {response.msg}, log_id: {response.get_log_id()}, resp: \n{json.dumps(json.loads(response.raw.content), indent=4, ensure_ascii=False)}")
            return

        # 处理业务结果
        lark.logger.info(lark.JSON.marshal(response.data, indent=4))


if __name__ == '__main__':
    import os
    feishu = FeishuAPI(app_id=os.getenv('APP_ID'), app_secret=os.getenv('APP_SECRET'), app_token=os.getenv('APP_TOKEN'))

    # feishu.send_message(receive_id="ou_7ff2829f40b7f6fa42ca81fa3487ac04",content="测试")

    feishu.to_feishu(table_id='tblF3OxQN7JAMBbs', data={
        "Bug 描述": 'bug_description',
        "提交人": 'submitter',
        "终端": '',
        "优先级": 'P2'
    })