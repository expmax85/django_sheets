import datetime
import json
from decimal import Decimal
from typing import Tuple, Dict, List

import httplib2
import requests
from django.core import serializers
from googleapiclient.discovery import build
from xml.etree import ElementTree

from oauth2client.service_account import ServiceAccountCredentials

from django.conf import settings

from bot import send_telegram
from test_app.models import Orders


class DataNotExist(Exception):
    pass


class TimeoutException(Exception):
    pass


SHEET_ID = '1ZZSVYG6IQLl7ZiYdweGIOUllFmpZMgYs_1tqScY2n54'
NAME_LIST = 'Лист1'
CURRENCY = 'USD'
CREDS_FILE = str(settings.BASE_DIR) + "/creds/credentials.json"


def get_service_acc(creds_json: str):

    scopes = ['https://www.googleapis.com/auth/spreadsheets']

    creds_service = ServiceAccountCredentials.from_json_keyfile_name(creds_json, scopes).authorize(httplib2.Http())
    return build('sheets', 'v4', http=creds_service)


def get_valute_currency() -> Decimal:
    url = 'https://www.cbr.ru/scripts/XML_daily.asp'
    response = requests.get(url, timeout=3)
    if not response:
        raise TimeoutException
    tree = ElementTree.fromstring(response.content)
    value = 0
    for elem in tree.iter('Valute'):
        if elem.find('CharCode').text == CURRENCY:
            value = Decimal(elem.find('Value').text.replace(',', '.'))
            return value
    if not value:
        raise


class GoogleSheetConnect:
    def __init__(self, cred_json: str, sheet_id: str, sheet_list: str) -> None:
        self.account = get_service_acc(cred_json)
        self.currency = get_valute_currency()
        self.sheet_id = sheet_id
        self.sheet_list = sheet_list

    def pull_sheet_data(self) -> List:
        sheet = self.account.spreadsheets()
        result = sheet.values().get(
            spreadsheetId=self.sheet_id,
            range=self.sheet_list).execute()
        values = result.get('values', [])
        if not values:
            raise DataNotExist('No data found.')
        return values[1:]

    def get_sheet_data(self) -> Tuple:
        sheet_data = tuple(item[1:] for item in self.pull_sheet_data())
        print(sheet_data)
        data_rub = tuple(round(Decimal(item[1]) * self.currency, 2) for item in sheet_data)
        for i in range(len(sheet_data)):
            sheet_data[i][2] = datetime.datetime.strptime(sheet_data[i][2], "%d.%m.%Y").strftime("%Y-%m-%d")
            sheet_data[i].append(data_rub[i])
        return sheet_data

    def get_data_db(self) -> Tuple:
        data_db = json.loads(serializers.serialize('json', Orders.objects.all()))
        return tuple(tuple(item['fields'].values()) for item in data_db)

    def get_changed_data(self, data_sheet: Tuple, data_db: Tuple) -> List:
        changed_data = []
        set_sheet = set(tuple('~'.join([str(item) for item in elem]) for elem in data_sheet))
        set_db = set(tuple('~'.join([str(value) for value in item]) for item in data_db))
        changes = set.difference(set_sheet, set_db)
        if changes:
            fields = ['order', 'price', 'delivery_date', 'rub_price']
            values = [item.split('~') for item in changes]
            for value in values:
                changed_data.append(dict(zip(fields, value)))
        return changed_data

    def get_deletion_orders(self, data_sheet: Tuple, data_db: Tuple) -> List:
        set_sheet = set(tuple(int(item[0]) for item in data_sheet))
        set_db = set(tuple(int(item[0]) for item in data_db))
        deletion_orders = set.difference(set_db, set_sheet)
        return list(deletion_orders)

    def create_in_db(self, data: List) -> None:
        Orders.objects.bulk_create(Orders(
            order=int(item['order']),
            price=Decimal(item['price']),
            delivery_date=datetime.datetime.strptime(item['delivery_date'], "%Y-%m-%d"),
            rub_price=Decimal(item['rub_price']))
                                   for item in data)

    def update_db(self, objs: Dict, data: List) -> None:
        for item in zip(list(objs.values()), data):
            item[0].price = item[1]['price']
            item[0].delivery_date = item[1]['delivery_date']
            item[0].rub_price = item[1]['rub_price']
        Orders.objects.bulk_update(list(objs.values()), ['price', 'delivery_date', 'rub_price'])

    def delete_from_db(self, deletion_orders: List) -> None:
        Orders.objects.filter(order__in=deletion_orders).delete()

    # def poll_update(self) -> None:
    #     data_sheet = self.get_sheet_data()
    #     data_db = self.get_data_db()
    #     deletion_orders = self.get_deletion_orders(data_sheet, data_db)
    #     if deletion_orders:
    #         self.delete_from_db(deletion_orders)
    #     changed_data = self.get_changed_data(data_sheet, data_db)
    #     if changed_data:
    #         update_objs = Orders.objects.in_bulk([int(item['order']) for item in changed_data], field_name='order')
    #         if update_objs:
    #             update_data = [item for item in changed_data if int(item['order']) in set(update_objs.keys())]
    #             create_data = [item for item in changed_data if int(item['order']) not in set(update_objs.keys())]
    #             self.update_db(update_objs, update_data)
    #             if create_data:
    #                 self.create_in_db(create_data)
    #         else:
    #             self.create_in_db(changed_data)
    #
    # def send_message_to_tm(self):
    #     today = datetime.date.today()
    #     delivered = Orders.objects.filter(delivery_date=today)
    #     for order in delivered:
    #         message = f'Order #{order.order} was delivered'
    #         send_telegram(message)


GOOGLE_SHEETS = GoogleSheetConnect(cred_json=CREDS_FILE, sheet_id=SHEET_ID, sheet_list=NAME_LIST)