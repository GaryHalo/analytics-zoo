#
# Copyright 2018 Analytics Zoo Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from bigdl.util.common import Sample as BSample, JTensor as BJTensor,\
    JavaCreator, _get_gateway, _py2java
import numpy as np
import os
import tempfile
import uuid

from urllib.parse import urlparse


def convert_to_safe_path(input_path, follow_symlinks=True):
    # resolves symbolic links
    if follow_symlinks:
        return os.path.realpath(input_path)
    # covert to abs path
    return os.path.abspath(input_path)


def to_list_of_numpy(elements):
    if isinstance(elements, np.ndarray):
        return [elements]
    elif np.isscalar(elements):
        return [np.array(elements)]
    elif not isinstance(elements, list):
        raise ValueError("Wrong type: %s" % type(elements))

    results = []
    for element in elements:
        if np.isscalar(element):
            results.append(np.array(element))
        elif isinstance(element, np.ndarray):
            results.append(element)
        else:
            raise ValueError("Wrong type: %s" % type(element))

    return results


def get_file_list(path, recursive=False):
    return callZooFunc("float", "listPaths", path, recursive)


def is_local_path(path):
    parse_result = urlparse(path)
    return len(parse_result.scheme.lower()) == 0 or parse_result.scheme.lower() == "file"


def append_suffix(prefix, path):
    # append suffix
    splits = path.split(".")
    if len(splits) > 0:
        file_name = prefix + "." + splits[-1]
    else:
        file_name = prefix

    return file_name


def save_file(save_func, path, **kwargs):

    if is_local_path(path):
        save_func(path, **kwargs)
    else:
        file_name = str(uuid.uuid1())
        file_name = append_suffix(file_name, path)
        temp_path = os.path.join(tempfile.gettempdir(), file_name)

        try:
            save_func(temp_path, **kwargs)
            if "overwrite" in kwargs:
                put_local_file_to_remote(temp_path, path, over_write=kwargs['overwrite'])
            else:
                put_local_file_to_remote(temp_path, path)
        finally:
            os.remove(temp_path)


def load_from_file(load_func, path):
    if is_local_path(path):
        return load_func(path)
    else:
        file_name = str(uuid.uuid1())
        file_name = append_suffix(file_name, path)
        temp_path = os.path.join(tempfile.gettempdir(), file_name)
        get_remote_file_to_local(path, temp_path)
        try:
            return load_func(temp_path)
        finally:
            os.remove(temp_path)


def get_remote_file_to_local(remote_path, local_path, over_write=False):
    callZooFunc("float", "getRemoteFileToLocal", remote_path, local_path, over_write)


def put_local_file_to_remote(local_path, remote_path, over_write=False):
    callZooFunc("float", "putLocalFileToRemote", local_path, remote_path, over_write)


def set_core_number(num):
    callZooFunc("float", "setCoreNumber", num)


def callZooFunc(bigdl_type, name, *args):
    """ Call API in PythonBigDL """
    gateway = _get_gateway()
    args = [_py2java(gateway, a) for a in args]
    error = Exception("Cannot find function: %s" % name)
    for jinvoker in JavaCreator.instance(bigdl_type, gateway).value:
        # hasattr(jinvoker, name) always return true here,
        # so you need to invoke the method to check if it exist or not
        try:
            api = getattr(jinvoker, name)
            java_result = api(*args)
            result = _java2py(gateway, java_result)
        except Exception as e:
            error = e
            if not ("does not exist" in str(e)
                    and "Method {}".format(name) in str(e)):
                raise e
        else:
            return result
    raise error


# TODO: change to bigdl's _java2py when update to bigdl 0.12.2
def _java2py(gateway, r, encoding="bytes"):
    from py4j.protocol import Py4JJavaError
    from py4j.java_gateway import JavaObject
    from py4j.java_collections import JavaArray, JavaList, JavaMap
    from pyspark import RDD
    from pyspark.serializers import PickleSerializer
    from pyspark.sql import DataFrame
    from bigdl.util.common import get_spark_context, _picklable_classes, get_spark_sql_context

    if isinstance(r, JavaObject):
        clsName = r.getClass().getSimpleName()
        # convert RDD into JavaRDD
        if clsName != 'JavaRDD' and clsName.endswith("RDD"):
            r = r.toJavaRDD()
            clsName = 'JavaRDD'

        if clsName == 'JavaRDD':
            jrdd = gateway.jvm.org.apache.spark.bigdl.api.python.BigDLSerDe.javaToPython(r)
            return RDD(jrdd, get_spark_context())

        if clsName == 'DataFrame':
            return DataFrame(r, get_spark_sql_context(get_spark_context()))

        if clsName == 'Dataset':
            return DataFrame(r, get_spark_sql_context(get_spark_context()))

        if clsName == "ImageFrame[]":
            return r

        if clsName in _picklable_classes:
            r = gateway.jvm.org.apache.spark.bigdl.api.python.BigDLSerDe.dumps(r)
        elif isinstance(r, (JavaArray, JavaList)) and len(r) != 0 \
                and isinstance(r[0], JavaObject) \
                and r[0].getClass().getSimpleName() in ['DataFrame', 'Dataset']:
            spark = get_spark_sql_context(get_spark_context())
            r = list(map(lambda x: DataFrame(x, spark), r))
        elif isinstance(r, (JavaArray, JavaList, JavaMap)):
            try:
                r = gateway.jvm.org.apache.spark.bigdl.api.python.BigDLSerDe.dumps(
                    r)
            except Py4JJavaError:
                pass  # not pickable
        if isinstance(r, (bytearray, bytes)):
            r = PickleSerializer().loads(bytes(r), encoding=encoding)
    return r


class JTensor(BJTensor):

    def __init__(self, storage, shape, bigdl_type="float", indices=None):
        super(JTensor, self).__init__(storage, shape, bigdl_type, indices)

    @classmethod
    def from_ndarray(cls, a_ndarray, bigdl_type="float"):
        """
        Convert a ndarray to a DenseTensor which would be used in Java side.
        """
        if a_ndarray is None:
            return None
        assert isinstance(a_ndarray, np.ndarray), \
            "input should be a np.ndarray, not %s" % type(a_ndarray)
        return cls(a_ndarray,
                   a_ndarray.shape,
                   bigdl_type)


class Sample(BSample):

    def __init__(self, features, labels, bigdl_type="float"):
        super(Sample, self).__init__(features, labels, bigdl_type)

    @classmethod
    def from_ndarray(cls, features, labels, bigdl_type="float"):
        features = to_list_of_numpy(features)
        labels = to_list_of_numpy(labels)
        return cls(
            features=[JTensor(feature, feature.shape) for feature in features],
            labels=[JTensor(label, label.shape) for label in labels],
            bigdl_type=bigdl_type)
