import sys
sys.path.append("../")
sys.path.append("../../")

import numpy as np
import json
import os
import random
import torch
from torchvision import datasets, transforms
import torch.optim as optim
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score, roc_curve

import argparse
from attack.distribution import distribution
from attack.sisa import sisa_train, sisa_test
from attack.model import MNISTNet, LeNet5, BadNet, FMNISTNet
from attack.util_file import create_dir
from attack.aggregation import aggregation
from attack.util_model import load_model

parser = argparse.ArgumentParser()
parser.add_argument(
    "--shards",
    default=None,
    type=int,
    help="Split the dataset in the given number of shards in an optimized manner (PLS-GAP partitionning) according to the given distribution, create the corresponding splitfile",
)
parser.add_argument(
    "--slices", default=1, type=int, help="Number of slices to use, default 1"
)
parser.add_argument(
    "--dataset",
    default="mnist",
    help="",
)
parser.add_argument(
    "--gpu",
    default=-1,
    type=int,
)
parser.add_argument(
    "--path",
    default="./path",
    help="",
)
parser.add_argument(
    "--output_type",
    default="argmax",
    help="Type of outputs to be used in aggregation, can be either argmax or softmax, default argmax",
)
parser.add_argument(
    "--experiment_id",
    default=1,
    type=int,
)
parser.add_argument(
    "--poison_num",
    default=50,
    type=int,
)
parser.add_argument(
    "--requests",
    default=0,
    type=int,
    help="Generate the given number of unlearning requests according to the given distribution and apply them directly to the splitfile",
)
parser.add_argument(
    "--mitigation_num",
    default=0,
    type=int,
)
args = parser.parse_args()

if args.gpu == -1:
    device = "cpu"
else:
    device = "cuda:" + str(args.gpu)

print("settings: ", args.shards, args.poison_num, args.mitigation_num, args.requests)

train_kwargs = {'batch_size': 100}
test_kwargs = {'batch_size': 1000}
transform = transforms.ToTensor()

train_dataset = datasets.MNIST('../../data', train=True, download=True, transform=transform)

path = os.path.join(args.path, str(args.shards) + "_" + str(args.slices) + "_100/", str(args.poison_num) + "_" + str(args.mitigation_num), str(args.experiment_id)) + "/"
create_dir(path)
args.path = path

[pidx, ori_label, plabel] = np.load(args.path + "setting.npy")

p_imgs = np.load(args.path + "poison_sample.npy", allow_pickle=True)
m_imgs = np.load(path + "mitigation_sample.npy", allow_pickle=True)

partition = np.load(args.path + "SNO_{}/splitfile.npy".format(args.shards), allow_pickle=True)
requests_all = np.load(args.path + "SNO_{}/requestfile-{}.npy".format(args.shards, args.requests), allow_pickle=True)
poison_all = np.array(range((len(train_dataset.data)-1),(len(train_dataset.data)-1+len(p_imgs))))
mitigation_all = np.array(range((len(train_dataset.data)-1+len(p_imgs)), (len(train_dataset.data)-1+len(p_imgs)+len(m_imgs))))
clean_all = np.array(list(set(range(len(train_dataset.data)))-set([pidx])))

avergae_auroc = 0
for _ in range(5):
    std_mitigation = []
    std_clean = []
    for sub_model in range(args.shards):
        train_dataset = datasets.MNIST('../../data', train=True, download=True, transform=transform)
        train_idx = partition[sub_model]
        requests = requests_all[sub_model]
        mitigation_idx = []
        for mi in mitigation_all:
            if mi in train_idx:
                mitigation_idx.append(mi)
        mitigation_idx = np.array(mitigation_idx) - (len(train_dataset.data) - 1 + len(p_imgs))

        # only consider the rest samples
        if len(requests) == 0:
            if len(mitigation_idx) == 0:
                continue
            rest_imgs = m_imgs[mitigation_idx]
        else:
            m_idx = np.array(list(set(mitigation_idx) - set(requests- (len(train_dataset.data) - 1 +len(p_imgs)))))
            if len(m_idx) == 0:
                continue
            rest_imgs = m_imgs[m_idx]

        train_loader = torch.utils.data.DataLoader(train_dataset, **test_kwargs)
        original_idx = np.random.choice(np.array(range(len(train_dataset.data))), len(rest_imgs), replace=False)


        train_dataset = datasets.MNIST('../../data', train=True, download=True, transform=transform)
        train_dataset.data = np.concatenate((train_dataset.data[original_idx].numpy(), rest_imgs))
        train_dataset.data = torch.tensor(train_dataset.data, dtype=torch.uint8)

        train_loader = torch.utils.data.DataLoader(train_dataset, **test_kwargs)

        probs = np.array([[]] * args.shards).tolist()
        for m in range(args.shards):
            model = LeNet5("mnist").to(device)
            model = load_model(model,
                               args.path + "SNO_{}/cache/shard-{}-{}.pt".format(args.shards, m, args.requests),
                               device)

            model.eval()
            count = 0
            with torch.no_grad():
                for x, y in train_loader:
                    x = x.to(device)
                    pred = F.softmax(model(x), dim=1)
                    for j in range(len(x)):
                        probs[m].append(pred.numpy()[j][train_dataset.targets[count]])
                        count += 1
        probs = np.array(probs)
        std_results = []
        for i in range(len(train_dataset.data)):
            std_results.append(np.std(probs[:, i]))
        std_mitigation = std_mitigation + std_results[-len(rest_imgs):]
        std_clean = std_clean + std_results[:-len(rest_imgs)]

    std = std_clean + std_mitigation
    label = np.concatenate([np.zeros([len(std_clean)]), np.ones([len(std_mitigation)])], axis=0)
    auroc = roc_auc_score(label, std)
    fpr, tpr, threshold = roc_curve(label, std)
    avergae_auroc += auroc
print(avergae_auroc / 5.0)



