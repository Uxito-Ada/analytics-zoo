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
from unittest import TestCase

import os
import pytest
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

from zoo.orca import init_orca_context, stop_orca_context
from zoo.orca.data.pandas import read_csv
from zoo.orca.data import SparkXShards
from zoo.orca.learn.pytorch import Estimator
from zoo.orca.learn.metrics import Accuracy
from zoo.orca.learn.trigger import EveryEpoch
from zoo.orca.learn.optimizers import SGD
from zoo.orca.learn.optimizers.schedule import Default
from zoo.orca import OrcaContext
import tempfile

resource_path = os.path.join(os.path.split(__file__)[0], "../../../resources")


def loss_func(input, target):
    return nn.CrossEntropyLoss().forward(input, target.flatten().long())


def transform(df):
    result = {
        "x": [df['user'].to_numpy(), df['item'].to_numpy()],
        "y": df['label'].to_numpy()
    }
    return result


def transform_del_y(d):
    result = {"x": d["x"]}
    return result


class SimpleModel(nn.Module):
    def __init__(self):
        super(SimpleModel, self).__init__()
        self.fc = nn.Linear(2, 2)

    def forward(self, x):
        x = self.fc(x)
        return F.log_softmax(x, dim=1)


class IdentityNet(nn.Module):
    def __init__(self):
        super().__init__()
        # need this line to avoid optimizer raise empty variable list
        self.fc1 = nn.Linear(50, 50)

    def forward(self, input_):
        return input_[:, 0]


class TestEstimatorForSpark(TestCase):

    def setUp(self):
        """ setup any state tied to the execution of the given method in a
        class.  setup_method is invoked for every test method of a class.
        """
        self.sc = init_orca_context(cores=4)

    def tearDown(self):
        """ teardown any state that was previously setup with a setup_method
        call.
        """
        stop_orca_context()

    def test_bigdl_pytorch_estimator_shard(self):

        model = SimpleModel()
        OrcaContext.pandas_read_backend = "pandas"
        file_path = os.path.join(resource_path, "orca/learn/ncf.csv")
        data_shard = read_csv(file_path)
        data_shard = data_shard.transform_shard(transform)

        with tempfile.TemporaryDirectory() as temp_dir_name:
            estimator = Estimator.from_torch(model=model, loss=loss_func,
                                             optimizer=SGD(learningrate_schedule=Default()),
                                             model_dir=temp_dir_name)
            estimator.fit(data=data_shard, epochs=4, batch_size=2, validation_data=data_shard,
                          validation_metrics=[Accuracy()], checkpoint_trigger=EveryEpoch())
            estimator.evaluate(data_shard, validation_metrics=[Accuracy()], batch_size=2)
            est2 = Estimator.from_torch(model=model, loss=loss_func, optimizer=None)
            est2.load(temp_dir_name, loss=loss_func)
            est2.fit(data=data_shard, epochs=8, batch_size=2, validation_data=data_shard,
                     validation_metrics=[Accuracy()], checkpoint_trigger=EveryEpoch())
            est2.evaluate(data_shard, validation_metrics=[Accuracy()], batch_size=2)
            pred_result = est2.predict(data_shard)
            pred_c = pred_result.collect()
            assert(pred_result, SparkXShards)
            pred_shard = data_shard.transform_shard(transform_del_y)
            pred_result2 = est2.predict(pred_shard)
            pred_c_2 = pred_result2.collect()
            assert (pred_c[0]["prediction"] == pred_c_2[0]["prediction"]).all()

    def test_bigdl_pytorch_estimator_dataframe(self):

        model = IdentityNet()
        rdd = self.sc.range(0, 100)
        from pyspark.sql import SparkSession
        spark = SparkSession(self.sc)
        df = rdd.map(lambda x: (np.random.randn(50).astype(np.float).tolist(),
                                [int(np.random.randint(0, 2, size=()))])
                     ).toDF(["feature", "label"])

        with tempfile.TemporaryDirectory() as temp_dir_name:
            estimator = Estimator.from_torch(model=model, loss=loss_func,
                                             optimizer=SGD(learningrate_schedule=Default()),
                                             model_dir=temp_dir_name)
            estimator.fit(data=df, epochs=4, batch_size=2, validation_data=df,
                          validation_metrics=[Accuracy()], checkpoint_trigger=EveryEpoch(),
                          feature_cols=["feature"], label_cols=["label"])
            estimator.evaluate(df, validation_metrics=[Accuracy()], batch_size=2,
                               feature_cols=["feature"], label_cols=["label"])
            result = estimator.predict(df, feature_cols=["feature"])
            result = np.concatenate([shard["prediction"] for shard in result.collect()])
            assert np.array_equal(result, np.array(range(100)).astype(np.float))

if __name__ == "__main__":
    pytest.main([__file__])
