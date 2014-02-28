# pylint: disable=C0103,E1101

import sys, os, unittest, json, uuid, tempfile, urllib2, shutil, time
from functools import wraps
try:
    from collections import OrderedDict  # Python 2.7
except ImportError:
    OrderedDict = dict
import socket
socket.setdefaulttimeout(10.0)

import bioblend
bioblend.set_stream_logger('test', level='INFO')
import bioblend.galaxy.objects.wrappers as wrappers
import bioblend.galaxy.objects.galaxy_instance as galaxy_instance
from bioblend.galaxy.client import ConnectionError


THIS_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_FN = os.path.join(THIS_DIR, 'data', 'paste_columns.ga')
with open(SAMPLE_FN) as F:
    WF_DICT = json.load(F)
FOO_DATA = 'foo\nbar\n'

URL = os.environ.get('BIOBLEND_GALAXY_URL', 'http://localhost:8080')
API_KEY = os.environ['BIOBLEND_GALAXY_API_KEY']


def is_reachable(url):
    res = None
    try:
        res = urllib2.urlopen(url, timeout=1)
    except urllib2.URLError:
        return False
    if res is not None:
        res.close()
    return True


def keep_trying(f):
    """
    Don't give up immediately if decorated test fails.

    This is useful for testing LibraryDataset contents, since LDs are
    not immediately ready after upload.
    """
    delay = 1
    max_retries = 5
    @wraps(f)
    def decorated(*args, **kwargs):
        r = 1
        while True:
            try:
                f(*args, **kwargs)
                break
            except AssertionError:
                if r >= max_retries:
                    raise RuntimeError('max n. of retries (%d) reached' % r)
            r += 1
            time.sleep(delay)
    return decorated


class MockWrapper(wrappers.Wrapper):

    BASE_ATTRS = frozenset(['a', 'b'])

    def __init__(self, *args, **kwargs):
        super(MockWrapper, self).__init__(*args, **kwargs)

    @property
    def gi_module(self):
        return super(MockWrapper, self).gi_module()


class TestWrapper(unittest.TestCase):

    def setUp(self):
        self.d = {'a' : 1, 'b' : [2, 3],  'c': {'x': 4}}
        self.assertRaises(TypeError, wrappers.Wrapper, self.d)
        self.w = MockWrapper(self.d)

    def test_initialize(self):
        for k in MockWrapper.BASE_ATTRS:
            self.assertEqual(getattr(self.w, k), self.d[k])
        self.w.a = 222
        self.w.b[0] = 222
        self.assertEqual(self.w.a, 222)
        self.assertEqual(self.w.b[0], 222)
        self.assertEqual(self.d['a'], 1)
        self.assertEqual(self.d['b'][0], 2)
        self.assertRaises(AttributeError, getattr, self.w, 'foo')
        self.assertRaises(AttributeError, setattr, self.w, 'foo', 0)

    def test_taint(self):
        self.assertFalse(self.w.is_modified)
        self.w.a = 111
        self.assertTrue(self.w.is_modified)

    def test_serialize(self):
        w = MockWrapper.from_json(self.w.to_json())
        self.assertEqual(w.wrapped, self.w.wrapped)

    def test_clone(self):
        w = self.w.clone()
        self.assertEqual(w.wrapped, self.w.wrapped)
        w.b[0] = 111
        self.assertEqual(self.w.b[0], 2)

    def test_kwargs(self):
        parent = MockWrapper({'a': 10})
        w = MockWrapper(self.d, parent=parent)
        self.assertEqual(w.parent, parent)
        self.assertRaises(AttributeError, setattr, w, 'parent', 0)


class TestWorkflow(unittest.TestCase):

    def setUp(self):
        self.id = '123'
        self.wf = wrappers.Workflow(WF_DICT, id=self.id)

    def test_initialize(self):
        self.assertEqual(self.wf.id, self.id)
        self.assertEqual(self.wf.name, WF_DICT['name'])
        self.assertFalse(self.wf.is_modified)
        self.wf.annotation = 'foo'
        self.assertTrue(self.wf.is_modified)

    def test_steps(self):
        step_dicts = [v for _, v in sorted(
            WF_DICT['steps'].items(), key=lambda t: int(t[0])
            )]
        for i, s in enumerate(self.wf.steps):
            self.assertTrue(isinstance(s, wrappers.Step))
            self.assertEqual(s.name, step_dicts[i]['name'])
            if step_dicts[i]['type'] == 'data_input':
                self.assertTrue(isinstance(s, wrappers.DataInput))
            if step_dicts[i]['type'] == 'tool':
                self.assertTrue(isinstance(s, wrappers.Tool))
            self.assertTrue(s.parent is self.wf)
        self.assertFalse(self.wf.is_modified)
        self.assertEqual(len(self.wf.data_inputs), 2)
        self.assertEqual(len(self.wf.tools), 1)

    def test_taint(self):
        self.assertFalse(self.wf.is_modified)
        self.wf.steps[0].annotation = 'foo'
        self.assertTrue(self.wf.is_modified)

    # may pass automatically if OrderedDict is dict
    def test_inputs(self):
        inputs = OrderedDict([
            ('100', {'label': 'foo', 'value': 'bar'}),
            ('99', {'label': 'boo', 'value': 'far'}),
            ])
        dummy_wf_info = wrappers.WorkflowInfo({
            'inputs': inputs,
            'steps': {'12284': {
                'id': 12284,
                'input_steps': {u'input': {u'source_step': 12285}},
                }},
            })
        wf = wrappers.Workflow(WF_DICT, wf_info=dummy_wf_info)
        self.assertEqual(wf.inputs, ['99', '100'])


class TestGalaxyInstance(unittest.TestCase):

    def setUp(self):
        self.gi = galaxy_instance.GalaxyInstance(URL, API_KEY)

    def test_library(self):
        name = 'test_%s' % uuid.uuid4().hex
        description, synopsis = 'D', 'S'
        lib = self.gi.libraries.create(
            name, description=description, synopsis=synopsis
            )
        self.assertEqual(lib.name, name)
        self.assertEqual(lib.description, description)
        self.assertEqual(lib.synopsis, synopsis)
        self.assertTrue(lib.id in [_.id for _ in self.gi.libraries.list()])
        lib.delete()
        self.assertFalse(lib.is_mapped)

    def test_history(self):
        name = 'test_%s' % uuid.uuid4().hex
        hist = self.gi.histories.create(name)
        self.assertEqual(hist.name, name)
        self.assertTrue(hist.id in [_.id for _ in self.gi.histories.list()])
        hist.delete(purge=True)
        self.assertFalse(hist.is_mapped)

    def assertWorkflowEqual(self, wf1, wf2):
        self.assertEqual(len(wf1.steps), len(wf2.steps))
        for step, istep in zip(wf1.steps, wf2.steps):
            self.assertEqual(step.name, istep.name)

    def test_workflow(self):
        wf = wrappers.Workflow(WF_DICT)
        wf.name = 'test_%s' % uuid.uuid4().hex
        imported = self.gi.workflows.import_new(wf)
        self.assertWorkflowEqual(imported, wf)
        for step, istep in zip(wf.steps, imported.steps):
            self.assertEqual(step.name, istep.name)
        self.assertTrue(imported.id in [_.id for _ in self.gi.workflows.list()])
        imported.delete()
        self.assertFalse(imported.is_mapped)

    def test_workflow_from_dict(self):
        imported = self.gi.workflows.import_new(WF_DICT)
        self.assertTrue(imported.id in [_.id for _ in self.gi.workflows.list()])
        imported.delete()

    def test_workflow_from_json(self):
        with open(SAMPLE_FN) as f:
            imported = self.gi.workflows.import_new(f.read())
        self.assertTrue(imported.id in [_.id for _ in self.gi.workflows.list()])
        imported.delete()

    # not very accurate:
    #   * we can't publish a wf from the API
    #   * we can't directly get another user's wf
    def test_workflow_from_shared(self):
        all_prevs = dict(
            (_.id, _) for _ in self.gi.workflows.get_previews(published=True)
            )
        pub_only_ids = set(all_prevs).difference(
            _.id for _ in self.gi.workflows.get_previews()
            )
        if pub_only_ids:
            wf_id = pub_only_ids.pop()
            imported = self.gi.workflows.import_shared(wf_id)
            self.assertTrue(isinstance(imported, wrappers.Workflow))
            imported.delete()
        else:
            print "skipped 'manually publish a workflow to run this test'"

    def test_workflow_info(self):
        imported = self.gi.workflows.import_new(WF_DICT)
        wi = imported.info
        inv_dag = {}
        for h, tails in wi.dag.iteritems():
            for t in tails:
                inv_dag.setdefault(str(t), set()).add(h)
        self.assertEqual(wi.inv_dag, inv_dag)
        heads = set(wi.dag)
        self.assertEqual(heads, set.union(*wi.inv_dag.itervalues()))
        tails = set(wi.inv_dag)
        self.assertEqual(tails, set.union(*wi.dag.itervalues()))
        ids = wi.sorted_step_ids()
        self.assertEqual(set(ids), heads | tails)
        for h, tails in wi.dag.iteritems():
            for t in tails:
                self.assertTrue(ids.index(h) < ids.index(t))
        imported.delete()

    def test_get_libraries(self):
        self.__test_multi_get('library')

    def test_get_histories(self):
        self.__test_multi_get('history')

    def test_get_workflows(self):
        self.__test_multi_get('workflow')

    def __test_multi_get(self, obj_type):
        if obj_type == 'library':
            create = self.gi.libraries.create
            get_objs = self.gi.libraries.list
            get_prevs = self.gi.libraries.get_previews
            del_kwargs = {}
        elif obj_type == 'history':
            create = self.gi.histories.create
            get_objs = self.gi.histories.list
            get_prevs = self.gi.histories.get_previews
            del_kwargs = {'purge': True}
        elif obj_type == 'workflow':
            def create(name):
                wf = wrappers.Workflow(WF_DICT)
                wf.name = name
                return self.gi.workflows.import_new(wf)
            get_objs = self.gi.workflows.list
            get_prevs = self.gi.workflows.get_previews
            del_kwargs = {}
        #--
        ids = lambda seq: set(_.id for _ in seq)
        names = ['test_%s' % uuid.uuid4().hex for _ in xrange(2)]
        objs = []
        try:
            objs = [create(_) for _ in names]
            self.assertTrue(ids(objs) <= ids(get_objs()))
            if obj_type != 'workflow':
                filtered = get_objs(name=names[0])
                self.assertEqual(len(filtered), 1)
                self.assertEqual(filtered[0].id, objs[0].id)
                del_id = objs[-1].id
                objs.pop().delete(**del_kwargs)
                self.assertTrue(del_id in ids(get_prevs(deleted=True)))
            else:
                # Galaxy appends info strings to imported workflow names
                prev = get_prevs()[0]
                filtered = get_objs(name=prev.name)
                self.assertEqual(len(filtered), 1)
                self.assertEqual(filtered[0].id, prev.id)
        finally:
            for o in objs:
                o.delete(**del_kwargs)


class TestLibraryContents(unittest.TestCase):

    URL = 'http://tools.ietf.org/rfc/rfc1866.txt'

    def setUp(self):
        self.gi = galaxy_instance.GalaxyInstance(URL, API_KEY)
        self.lib = self.gi.libraries.create('test_%s' % uuid.uuid4().hex)

    def tearDown(self):
        self.lib.delete()

    def test_folder(self):
        name, desc = 'test_%s' % uuid.uuid4().hex, 'D'
        folder = self.lib.create_folder(name, description=desc)
        self.assertEqual(folder.name, name)
        self.assertEqual(folder.description, desc)
        self.assertEqual(folder.container_id, self.lib.id)

    def test_dataset(self):
        folder = self.lib.create_folder('test_%s' % uuid.uuid4().hex)
        self.assertEqual(0, len(self.lib.dataset_ids))
        ds = self.lib.upload_data(FOO_DATA, folder=folder)
        self.assertEqual(ds.container_id, self.lib.id)
        # ensure the list of dataset ids has been updated correctly
        self.assertEqual(1, len(self.lib.dataset_ids))
        lib = self.gi.libraries.get(self.lib.id)
        self.assertEqual(lib.dataset_ids, self.lib.dataset_ids)
        ds_id = lib.dataset_ids[0]
        self.assertEqual(ds_id, ds.id)
        self.assertEqual(self.lib.get_dataset(ds_id).id, ds.id)

    def test_dataset_from_url(self):
        if is_reachable(self.URL):
            ds = self.lib.upload_from_url(self.URL)
            self.assertEqual(ds.container_id, self.lib.id)
            assert isinstance(ds, wrappers.Dataset)
        else:
            print "skipped 'url not reachable'"

    def test_dataset_from_local(self):
        with tempfile.NamedTemporaryFile(prefix='bioblend_test_') as f:
            f.write(FOO_DATA)
            f.flush()
            ds = self.lib.upload_from_local(f.name)
        assert isinstance(ds, wrappers.Dataset)
        self.assertEqual(ds.container_id, self.lib.id)
        lib = self.gi.libraries.get(self.lib.id)
        self.assertEqual(lib.dataset_ids, self.lib.dataset_ids)

    def test_datasets_from_fs(self):
        tempdir = tempfile.mkdtemp(prefix='bioblend_test_')
        try:
            fnames = [os.path.join(tempdir, 'data%d.txt' % i)
                      for i in xrange(3)]
            for fn in fnames:
                with open(fn, 'w') as f:
                    f.write(FOO_DATA)
            dss = self.lib.upload_from_galaxy_fs(
                fnames[:2], link_data_only='link_to_files'
                )
            self.assertEqual(len(dss), 2)
            for ds, fn in zip(dss, fnames):
                self.assertEqual(ds.container_id, self.lib.id)
                self.assertEqual(ds.file_name, fn)
            dss = self.lib.upload_from_galaxy_fs(fnames[-1])
            self.assertEqual(len(dss), 1)
            self.assertNotEqual(dss[0].file_name, fnames[-1])
            lib = self.gi.libraries.get(self.lib.id)
            self.assertEqual(lib.dataset_ids, self.lib.dataset_ids)
        finally:
            shutil.rmtree(tempdir)


class TestLDContents(unittest.TestCase):

    def setUp(self):
        self.gi = galaxy_instance.GalaxyInstance(URL, API_KEY)
        self.lib = self.gi.libraries.create('test_%s' % uuid.uuid4().hex)
        self.ds = self.lib.upload_data(FOO_DATA)

    def tearDown(self):
        self.lib.delete()

    @keep_trying
    def test_dataset_get_stream(self):
        for idx, c in enumerate(self.ds.get_stream(chunk_size=1)):
            self.assertEqual(str(FOO_DATA[idx]), c)

    @keep_trying
    def test_dataset_peek(self):
        fetched_data = self.ds.peek(chunk_size=4)
        self.assertEqual(FOO_DATA[0:4], fetched_data)

    @keep_trying
    def test_dataset_download(self):
        with tempfile.TemporaryFile() as f:
            self.ds.download(f)
            f.seek(0)
            self.assertEqual(FOO_DATA, f.read())

    @keep_trying
    def test_dataset_get_contents(self):
        self.assertEqual(FOO_DATA, self.ds.get_contents())


class TestHistoryContents(unittest.TestCase):

    def setUp(self):
        self.gi = galaxy_instance.GalaxyInstance(URL, API_KEY)
        self.hist = self.gi.histories.create('test_%s' % uuid.uuid4().hex)
        self.lib = self.gi.libraries.create('test_%s' % uuid.uuid4().hex)

    def tearDown(self):
        self.hist.delete(purge=True)
        self.lib.delete()

    def test_dataset_upload(self):
        lds = self.gi.libraries.upload_data(self.lib, FOO_DATA)
        hda = self.gi.histories.import_dataset(self.hist, lds)
        self.assertTrue(isinstance(hda, wrappers.HistoryDatasetAssociation))
        self.assertEqual(hda.container_id, self.hist.id)
        updated_hist = self.gi.histories.get(self.hist.id)
        self.assertTrue(hda.id in updated_hist.dataset_ids)

    def test_delete(self):
        hist = self.gi.histories.create('test_%s' % uuid.uuid4().hex)
        hist_id = hist.id
        hist.delete(purge=True)
        self.assertFalse(hist.is_mapped)
        try:
            h = self.gi.histories.get(hist_id)
            self.fail("Expected ConnectionError but GET returned %s" % str(h))
        except ConnectionError:
            pass

    def test_import_dataset(self):
        lds = self.gi.libraries.upload_data(self.lib, FOO_DATA)
        self.assertEqual(len(self.hist.dataset_ids), 0)
        hda = self.hist.import_dataset(lds)
        self.assertTrue(isinstance(hda, wrappers.HistoryDatasetAssociation))
        self.assertEqual(hda.container_id, self.hist.id)
        self.assertEqual(len(self.hist.dataset_ids), 1)
        self.assertTrue(hda.id in self.hist.dataset_ids)

    def test_get_dataset(self):
        lds = self.gi.libraries.upload_data(self.lib, FOO_DATA)
        hda = self.hist.import_dataset(lds)
        retrieved = self.hist.get_dataset(hda.id)
        self.assertEqual(hda.id, retrieved.id)

    def test_get_datasets(self):
        lds = [self.gi.libraries.upload_data(self.lib, FOO_DATA),
               self.gi.libraries.upload_data(self.lib, 'foo2\nbar2\n')]
        hdas = [self.hist.import_dataset(_) for _ in lds]
        datasets = self.hist.get_datasets()
        self.assertEqual(len(lds), len(datasets))
        self.assertEqual([_.id for _ in hdas], [_.id for _ in datasets])


class TestHDAContents(unittest.TestCase):

    def setUp(self):
        self.gi = galaxy_instance.GalaxyInstance(URL, API_KEY)
        self.hist = self.gi.histories.create('test_%s' % uuid.uuid4().hex)
        self.lib = self.gi.libraries.create('test_%s' % uuid.uuid4().hex)
        ld = self.lib.upload_data(FOO_DATA)
        self.ds = self.hist.import_dataset(ld)
        self.ds.wait()

    def tearDown(self):
        super(TestHDAContents, self).tearDown()

    def test_dataset_get_stream(self):
        for idx, c in enumerate(self.ds.get_stream(chunk_size=1)):
            self.assertEqual(str(FOO_DATA[idx]), c)

    def test_dataset_peek(self):
        fetched_data = self.ds.peek(chunk_size=4)
        self.assertEqual(FOO_DATA[0:4], fetched_data)

    def test_dataset_download(self):
        with tempfile.TemporaryFile() as f:
            self.ds.download(f)
            f.seek(0)
            data = f.read()
            self.assertEqual(FOO_DATA, data)

    def test_dataset_get_contents(self):
        self.assertEqual(FOO_DATA, self.ds.get_contents())


class TestRunWorkflow(unittest.TestCase):

    def setUp(self):
        self.gi = galaxy_instance.GalaxyInstance(URL, API_KEY)
        self.lib = self.gi.libraries.create('test_%s' % uuid.uuid4().hex)
        self.wf = self.gi.workflows.import_new(WF_DICT)
        self.contents = ['one\ntwo\n', '1\n2\n']
        self.inputs = [self.gi.libraries.upload_data(self.lib, c)
                       for c in self.contents]
        self.hist_name = 'test_%s' % uuid.uuid4().hex

    def tearDown(self):
        self.wf.delete()
        self.lib.delete()

    def __check_res(self, res, sep):
        exp_rows = zip(*(_.splitlines() for _ in self.contents))
        exp_res = "\n".join(sep.join(t) for t in exp_rows)
        self.assertEqual(res.strip(), exp_res)

    def __test(self, existing_hist=False, params=False):
        if existing_hist:
            hist = self.gi.histories.create(self.hist_name)
        else:
            hist = self.hist_name
        if params:
            params = {self.wf.tools[0].tool_id: {'delimiter': 'U'}}
            sep = '_'  # 'U' maps to '_' in the paste tool
        else:
            params = None
            sep = '\t'  # default
        sys.stderr.write(os.linesep)
        outputs, out_hist = self.wf.run(
            self.inputs, hist, params=params, wait=True, polling_interval=1
            )
        self.assertEqual(len(outputs), 1)
        out_ds = outputs[0]
        self.assertTrue(out_ds.id in out_hist.dataset_ids)
        res = out_ds.get_contents()
        self.__check_res(res, sep)
        if existing_hist:
            self.assertEqual(out_hist.id, hist.id)
        out_hist.delete(purge=True)

    def test_existing_history(self):
        self.__test(existing_hist=True)

    def test_new_history(self):
        self.__test(existing_hist=False)

    def test_params(self):
        self.__test(params=True)


# XXX: don't use TestLoader.loadTests* until support for Python 2.6 is dropped
def suite():
    loader = unittest.TestLoader()
    s = unittest.TestSuite()
    s.addTests([loader.loadTestsFromTestCase(c) for c in (
        TestWrapper,
        TestWorkflow,
        TestGalaxyInstance,
        TestLibraryContents,
        TestLDContents,
        TestHistoryContents,
        TestHDAContents,
        TestRunWorkflow,
        )])
    return s


if __name__ == '__main__':
    # By default, run all tests.  To run specific tests, do the following:
    #   python -m unittest <module>.<class>.<test_method>
    tests = suite()
    RUNNER = unittest.TextTestRunner(verbosity=2)
    RUNNER.run(tests)
