"""Own the fixed bootstrap container and seed only public request fixture dates."""
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
spec=importlib.util.spec_from_file_location('rp_home_wasm_linux_owner',
    ROOT/'experiments/php-wasix-wp-bootstrap-probe/linux_database.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
require=base.require
STAMP='2026-01-01 00:00:00'
SEED=("UPDATE rp_wpdb_probe.rp_posts SET post_date='"+STAMP+"',post_date_gmt='"+STAMP
    +"',post_modified='"+STAMP+"',post_modified_gmt='"+STAMP
    +"' WHERE ID IN (1,2) AND post_status='publish' AND post_type IN ('post','page');\n"
    "UPDATE rp_wpdb_probe.rp_comments SET comment_date='"+STAMP+"',comment_date_gmt='"+STAMP
    +"' WHERE comment_ID=1 AND comment_post_ID=1;\n").encode('ascii')
ORACLES={'mysql':'7cf6c805a7863be81b81fd64177347e2fdea6170bd37594ad446845a90d6bf9a',
    'mariadb':'d79130b07427eab57d54275341b4bbd271bd53c63499b1d47139ab3b4c88831b'}


class Database(base.Database):
    def home_request(self):
        value=super().request('read')
        value.update(schema='recastpress.php-wasix-wp-request-input.v0.5',mode='home')
        return value

    def seed_public_dates(self):
        self.inspect()
        path=ROOT/('experiments/wp-request-probe/evidence/windows-'+self.backend+'-native.json')
        require(path.resolve(strict=True)==path and path.stat().st_size<=1024*1024,'fixed_home_native_oracle')
        wire=path.read_bytes()
        require(hashlib.sha256(wire).hexdigest()==ORACLES[self.backend],'fixed_home_native_oracle_changed')
        native=json.loads(wire)
        require(native['passed'] is True and native['fixed_public_date_seed']['sql_sha256']
            ==hashlib.sha256(SEED).hexdigest(),'same_native_public_date_seed')
        result=self.run('seed_same_native_public_dates',self.client(),timeout=10,input_bytes=SEED)
        require(not result.stdout.strip() and not result.stderr,'public_seed_unexpected_output')
        self.report['fixed_public_date_seed']={'timestamp':STAMP,'sql_sha256':hashlib.sha256(SEED).hexdigest(),
            'actual_database_updated':True,'response_body_normalized':False}
        self.verify_public_dates('before_home')

    def verify_public_dates(self,phase):
        require(phase in ('before_home','after_home'),'closed_public_date_phase')
        self.inspect()
        result=self.run('independent_public_post_dates_'+phase,self.client()+[
            "--execute=SELECT ID,post_date,post_date_gmt,post_modified,post_modified_gmt FROM rp_wpdb_probe.rp_posts "
            "WHERE ID IN (1,2) AND post_status='publish' AND post_type IN ('post','page') ORDER BY ID"])
        actual=[line.split('\t') for line in result.stdout.decode('ascii').strip().splitlines()]
        require(actual==[[str(i),*([STAMP]*4)] for i in (1,2)],'same_native_public_post_dates')
        result=self.run('independent_public_comment_dates_'+phase,self.client()+[
            "--execute=SELECT comment_ID,comment_post_ID,comment_date,comment_date_gmt FROM rp_wpdb_probe.rp_comments "
            "WHERE comment_ID=1 AND comment_post_ID=1"])
        actual=[line.split('\t') for line in result.stdout.decode('ascii').strip().splitlines()]
        require(actual==[['1','1',STAMP,STAMP]],'same_native_public_comment_dates')
        self.report.setdefault('public_date_verification',{})[phase]={'post_ids':[1,2],
            'comment_ids':[1],'timestamp':STAMP,'verified':True}

    def final_rows(self):
        super().final_rows()
        self.verify_public_dates('after_home')
