import datetime as dt
import json
import pathlib
import tempfile
import unittest
import uuid
from unittest import mock
from usage_accounting import UsageStore, parse_usernames
from reservations import ReservationStore, ReservationError


def iso(t):
    return dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1800000000.0;self.start=self.now
        self.path=pathlib.Path(self.tmp.name)/'db.sqlite3'
        self.store=UsageStore(self.path,clock=lambda:self.now)

    def sample(self,t,owners=None,complete=True,boot='one',accounts=None):
        self.now=t
        self.store.ingest({'machine_id':'10.20.6.38','observed_at':iso(t),'gpu_usage':{'complete':complete,'owners':owners or {},'boot_id':boot,'accounts':accounts if accounts is not None else [{'username':'wkli','uid':1001}]}})

    def test_multiple_account_format(self):
        self.assertEqual(parse_usernames('  zfgao   wkli  '),['zfgao','wkli'])
        self.assertEqual(parse_usernames('wkli'),['wkli'])
        for bad in ('', '   ', 'wkli,wfgao', 'wkli，zfgao', 'wkli\tzfgao', 'wkli\nzfgao', 'wkli\n', 'wkli\u00a0zfgao', 'wkli　zfgao', '服务器账号 wkli', 'wkli wkli', '2wkli', 'a'*65, ' '.join('user'+str(i) for i in range(11)), None, ['wkli']):
            with self.subTest(bad=bad):
                self.assertRaises(ValueError,parse_usernames,bad)

    def test_multiple_accounts_union_cards_and_person_statistics(self):
        t=self.start
        accounts=[{'username':'wkli','uid':1001},{'username':'zfgao','uid':1002}]
        self.sample(t,{'0':[1001,1002]},accounts=accounts)
        registrations=ReservationStore(self.path,clock=lambda:self.now)
        data={'machine_id':'10.20.6.38','server_username':' zfgao   wkli ','name':'虚构','wechat':'fixture','gpu_count':2,'gpu_indices':[0,1],'start_at':iso(t),'end_at':iso(t+3600)}
        record=registrations.create(data,str(uuid.uuid4()))
        self.assertEqual(record['server_username'],'zfgao wkli')
        self.assertEqual(record['usage_segments'][-1]['uids'],[1001,1002])
        self.sample(t+30,{'0':[1001],'1':[1002]},accounts=accounts)
        self.sample(t+60,accounts=accounts)
        measured=registrations.list_records()[0]['usage']
        self.assertAlmostEqual(measured['occupied_gpu_hours'],90/3600,places=6)
        self.assertEqual(measured['usage_percent'],75)
        self.assertEqual(measured['coverage_percent'],100)
        self.assertEqual(registrations.statistics(7)['people'][0]['usage'],{k:v for k,v in measured.items() if k!='account_status'})
        self.assertEqual(ReservationStore(self.path,clock=lambda:self.now).list_records()[0]['usage'],measured)

    def test_legacy_single_uid_and_account_edit_do_not_backfill(self):
        t=self.start
        accounts=[{'username':'wkli','uid':1001},{'username':'zfgao','uid':1002},{'username':'alias','uid':1002}]
        self.sample(t,{'0':[1001],'1':[1002]},accounts=accounts)
        registrations=ReservationStore(self.path,clock=lambda:self.now)
        data={'machine_id':'10.20.6.38','server_username':'wkli','name':'虚构','wechat':'fixture','gpu_count':2,'gpu_indices':[0,1],'start_at':iso(t),'end_at':iso(t+3600)}
        record=registrations.create(data,str(uuid.uuid4()))
        # Simulate the persisted single-account format from the previous release.
        record['usage_segments'][0].pop('uids')
        record['usage_segments'][0]['uid']=1001
        with self.store.connect() as db:
            db.execute('UPDATE reservations SET data=? WHERE id=?',(json.dumps(record),record['id']))
        self.sample(t+60,{'0':[1001],'1':[1002]},accounts=accounts)
        self.assertEqual(registrations.list_records()[0]['usage']['usage_percent'],50)
        changed=registrations.update(record['id'],{'server_username':'wkli zfgao alias'},1,str(uuid.uuid4()))
        self.assertEqual(changed['usage_segments'][0]['uid'],1001)
        self.assertEqual(changed['usage_segments'][1]['uids'],[1001,1002])
        self.sample(t+120,accounts=accounts)
        measured=registrations.list_records()[0]['usage']
        self.assertEqual(measured['usage_percent'],75)
        self.assertAlmostEqual(measured['occupied_gpu_hours'],180/3600,places=6)

    def test_pending_group_waits_for_all_accounts_without_backfill(self):
        t=self.start
        registrations=ReservationStore(self.path,clock=lambda:self.now)
        data={'machine_id':'10.20.6.38','server_username':'wkli zfgao','name':'虚构','wechat':'fixture','gpu_count':1,'gpu_indices':[0],'start_at':iso(t),'end_at':iso(t+3600)}
        record=registrations.create(data,str(uuid.uuid4()))
        self.sample(t+30,{'0':[1001]})
        self.assertEqual(registrations.list_records()[0]['usage']['account_status'],'pending')
        self.assertEqual(len(registrations.history(record['id'])),1)
        accounts=[{'username':'wkli','uid':1001},{'username':'zfgao','uid':1002}]
        self.sample(t+60,{'0':[1001,1002]},accounts=accounts)
        self.sample(t+90,{'0':[1001,1002]},accounts=accounts)
        result=registrations.list_records()[0]
        self.assertEqual(result['usage']['account_status'],'bound')
        self.assertEqual(result['usage']['coverage_percent'],33.33)
        self.assertEqual(result['usage']['usage_percent'],100)
        self.assertEqual(len(registrations.history(record['id'])),2)

    def test_occupancy_dedup_and_gaps_and_restarts(self):
        t=self.start
        self.sample(t,{'0':[1001,1001]});self.sample(t+30,{'0':[1001]});self.sample(t+60,{})
        a=('10.20.6.38',1001,[0,1],2,t,t+120)
        r=self.store.measure([a,a],t,t+120)
        self.assertAlmostEqual(r['occupied_gpu_hours'],60/3600,places=6)
        self.assertEqual(r['usage_percent'],50)
        self.assertEqual(r['coverage_percent'],50)
        # Duplicate/out-of-order delivery cannot inflate time.
        self.sample(t+60,{'0':[1001]});self.sample(t+15,{'0':[1001]})
        self.assertEqual(r,self.store.measure([a],t,t+120))
        # Broken interval and machine reboot are not counted as idle or occupied.
        self.sample(t+90,complete=False);self.sample(t+120);self.sample(t+150,boot='two')
        self.assertEqual(r,self.store.measure([a],t,t+120))
        self.assertIsNone(self.store.measure([('10.20.6.38',None,[],1,t,t+60)],t,t+60)['usage_percent'])

    def test_other_uid_and_wrong_gpu_never_attributed(self):
        t=self.start
        self.sample(t,{'0':[2000],'1':[1001]});self.sample(t+60)
        r=self.store.measure([('10.20.6.38',1001,[0],1,t,t+60)],t,t+60)
        self.assertEqual(r['occupied_gpu_hours'],0)
        self.assertEqual(r['coverage_percent'],100)
        # No selected indices: cap at the registered card count.
        self.assertEqual(self.store.measure([('10.20.6.38',1001,[],1,t,t+60)],t,t+60)['usage_percent'],100)

    def test_binding_legacy_and_account_changes_preserve_attribution(self):
        t=self.start;self.sample(t,{'0':[1001]})
        registrations=ReservationStore(self.path,clock=lambda:self.now)
        data={'machine_id':'10.20.6.38','server_username':'wkli','name':'虚构测试','wechat':'fixture','gpu_count':1,'gpu_indices':[0],'start_at':iso(t),'end_at':iso(t+3600)}
        record=registrations.create(data,str(uuid.uuid4()))
        self.sample(t+30,{'0':[1001]});self.sample(t+60,{'0':[1001]})
        measured=registrations.list_records()[0]['usage']
        self.assertEqual(measured['usage_percent'],100)
        self.assertEqual(measured['coverage_percent'],100)
        self.assertEqual(registrations.statistics(7)['people'][0]['usage']['usage_percent'],100)
        self.assertRaises(ReservationError,registrations.create,dict(data,server_username='missing'),str(uuid.uuid4()))
        changed=registrations.update(record['id'],{'gpu_indices':[1]},1,str(uuid.uuid4()))
        self.sample(t+90,{'0':[1001]});self.sample(t+120)
        r=registrations.list_records()[0]['usage']
        self.assertEqual(r['usage_percent'],50)
        self.assertEqual(len(changed['usage_segments']),2)
        # Coverage survives closing and reopening the store.
        reopened=ReservationStore(self.path,clock=lambda:self.now)
        self.assertEqual(reopened.list_records()[0]['usage'],r)

    def test_offline_account_pending_then_verified_without_backfill(self):
        t=self.start
        registrations=ReservationStore(self.path,clock=lambda:self.now)
        with mock.patch.object(ReservationStore, '_allocate_now'):
            record=registrations.create({'machine_id':'10.20.6.38','server_username':'wkli','name':'虚构','wechat':'fixture','gpu_count':1,'gpu_indices':[0],'start_at':iso(t),'end_at':iso(t+3600)},str(uuid.uuid4()))
        self.assertEqual(registrations.list_records()[0]['usage']['account_status'],'pending')
        self.sample(t+30,{'0':[1001]});self.sample(t+60,{'0':[1001]})
        result=registrations.list_records()[0]
        self.assertEqual(result['usage']['account_status'],'bound')
        self.assertEqual(result['usage']['coverage_percent'],50)
        self.assertEqual(result['usage']['usage_percent'],100)
        self.assertEqual(registrations.history(record['id'])[-1]['action'],'account_verified')

    def test_retention_and_long_gaps(self):
        t=self.start;self.sample(t);self.sample(t+60);self.sample(t+300)
        r=self.store.measure([('10.20.6.38',1001,[],1,t,t+300)],t,t+300)
        self.assertEqual(r['coverage_percent'],20)
        self.sample(t+36*86400)
        with self.store.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM usage_intervals').fetchone()[0],0)

if __name__=='__main__':unittest.main()
