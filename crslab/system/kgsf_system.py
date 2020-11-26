# @Time   : 2020/11/22
# @Author : Kun Zhou
# @Email  : francis_kun_zhou@163.com

# UPDATE:
# @Time   : 2020/11/24
# @Author : Kun Zhou
# @Email  : francis_kun_zhou@163.com

import torch
from loguru import logger
from tqdm import tqdm

from crslab.evaluator.metrics import AverageMetric, PPLMetric
from crslab.system.base_system import BaseSystem
from crslab.system.utils import nice_report


class KGSFSystem(BaseSystem):
    r"""S3RecTrainer is designed for S3Rec, which is a self-supervised learning based sequentail recommenders.
        It includes two training stages: pre-training ang fine-tuning.

        """

    def __init__(self, config, train_dataloader, valid_dataloader, test_dataloader, side_data):
        super(KGSFSystem, self).__init__(config, train_dataloader, valid_dataloader, test_dataloader, side_data)
        self.pretrain_epoch = self.config['optim']['pretrain']['epoch']
        self.rec_epoch = self.config['optim']['rec']['epoch']
        self.conv_epoch = self.config['optim']['conv']['epoch']

        self.pretrain_batch_size = self.config['batch_size']['pretrain']
        self.rec_batch_size = self.config['batch_size']['rec']
        self.conv_batch_size = self.config['batch_size']['conv']
        self.movie_ids = self.train_dataloader.get_movie_ids()

    def init_optim(self):
        r"""Init the Optimizer

        Returns:
            torch.optim: the optimizer
        """
        self.pretrain_optimizer = self.build_optimizer(self.config, self.model.parameters())
        self.pretrain_scheduler, self.pretrain_warmup_scheduler = self.build_lr_scheduler(self.config,
                                                                                          self.pretrain_optimizer)

        self.rec_optimizer = self.build_optimizer(self.config, self.model.parameters())
        self.rec_scheduler, self.rec_warmup_scheduler = self.build_lr_scheduler(self.config, self.rec_optimizer)

        self.model.stem_conv_parameters()
        self.conv_optimizer = self.build_optimizer(self.config, self.model.parameters())
        self.conv_scheduler, self.conv_warmup_scheduler = self.build_lr_scheduler(self.config, self.conv_optimizer)

    def rec_evaluate(self, rec_predict, movie_label):
        rec_predict = rec_predict.cpu().detach()
        rec_predict = rec_predict[:, self.movie_ids]
        _, rec_ranks = torch.topk(rec_predict, 50, dim=-1)
        movie_label = movie_label.cpu().detach()
        for rec_rank, movie in zip(rec_ranks, movie_label):
            movie = self.movie_ids.index(movie.item())
            self.evaluator.get_evaluate_fn('rec')(rec_rank, movie)

    def conv_evaluate(self, prediction, response):
        response = response.cpu().detach()
        for p, r in zip(prediction, response):
            p_str = self.inds2txt(p)
            r_str = self.inds2txt(r)
            self.evaluator.get_evaluate_fn('conv')(p_str, [r_str])

    def step(self, batch, stage, mode):
        '''
        stage=['pretrain','rec','conv']
        mode=['train','val']
        '''
        # encode user
        batch = [ele.to(self.device) for ele in batch]
        if stage == 'pretrain':
            info_loss = self.model.pretrain_infomax(batch)
            self.backward(self.pretrain_optimizer, info_loss,
                          self.pretrain_warmup_scheduler, self.pretrain_scheduler)
            info_loss = info_loss.item()
            self.evaluator.add_metric("other", "info_loss", AverageMetric(info_loss))
        elif stage == 'rec':
            rec_loss, info_loss, rec_predict = self.model.recommender(batch, mode)
            loss = rec_loss + 0.025 * info_loss
            if mode == "train":
                self.backward(self.rec_optimizer, loss, self.rec_warmup_scheduler, self.rec_scheduler)
            else:
                self.rec_evaluate(rec_predict, batch[-1])
            info_loss = info_loss.item()
            self.evaluator.add_metric("other", "info_loss", AverageMetric(info_loss))
            rec_loss = rec_loss.item()
            self.evaluator.add_metric("rec", "rec_loss", AverageMetric(rec_loss))
        elif stage == "conv":
            if mode == "train":
                conv_loss = self.model.conversation(batch, mode)

                self.backward(self.conv_optimizer, conv_loss, self.conv_warmup_scheduler, self.conv_scheduler)
                # eval
                conv_loss = conv_loss.item()
                self.evaluator.add_metric("conv", "gen_loss", AverageMetric(conv_loss))
                self.evaluator.add_metric("conv", "ppl", PPLMetric(conv_loss))
            else:
                pred = self.model.conversation(batch, mode)
                self.conv_evaluate(pred, batch[-1])

    def pretrain(self, debug=False):
        if debug:
            pretrain_batches = self.valid_dataloader.get_pretrain_data(self.pretrain_batch_size)
        else:
            pretrain_batches = self.train_dataloader.get_pretrain_data(self.pretrain_batch_size)
        for epoch_num in range(self.pretrain_epoch):
            logger.info('[{}] epoch start for pretraining', str(epoch_num))
            for batch in tqdm(pretrain_batches):
                self.step(batch, stage="pretrain", mode='train')

    def train_recommender(self, debug=False):
        if debug:
            rec_train_batches = self.valid_dataloader.get_rec_data(self.rec_batch_size)
            rec_val_batches = rec_train_batches
            rec_test_batches = rec_val_batches
        else:
            rec_train_batches = self.train_dataloader.get_rec_data(self.rec_batch_size)
            rec_val_batches = self.valid_dataloader.get_rec_data(self.rec_batch_size)
            rec_test_batches = self.test_dataloader.get_rec_data(self.rec_batch_size)

        for epoch_num in range(self.rec_epoch):
            logger.info('[{}] epoch start for training recommender model', str(epoch_num))
            for batch in tqdm(rec_train_batches):
                self.step(batch, stage='rec', mode='train')
            with torch.no_grad():
                for batch in rec_val_batches:
                    self.step(batch, stage='rec', mode='val')
                report = self.evaluator.report()
                self.reset_metrics()
                logger.info(nice_report(report))
            if self.stop == True:
                break
        with torch.no_grad():
            for batch in rec_test_batches:
                self.step(batch, stage='rec', mode='val')
            report = self.evaluator.report()
            self.reset_metrics()
            logger.info(nice_report(report))

    def train_conversation(self, debug=False):
        if debug:
            conv_train_batches = self.valid_dataloader.get_conv_data(self.conv_batch_size)
            conv_val_batches = conv_train_batches
            conv_test_batches = conv_val_batches
        else:
            conv_train_batches = self.train_dataloader.get_conv_data(self.conv_batch_size)
            conv_val_batches = self.valid_dataloader.get_conv_data(self.conv_batch_size)
            conv_test_batches = self.test_dataloader.get_conv_data(self.conv_batch_size)
        for epoch_num in range(self.conv_epoch):
            logger.info('[{}] epoch start for training conversational model', str(epoch_num))
            for batch in tqdm(conv_train_batches):
                self.step(batch, stage='conv', mode='train')
            with torch.no_grad():
                for batch in conv_val_batches:
                    self.step(batch, stage='conv', mode='val')
                report = self.evaluator.report()
                self.reset_metrics()
                logger.info(nice_report(report))
            if self.stop == True:
                break
        with torch.no_grad():
            for batch in conv_test_batches:
                self.step(batch, stage='conv', mode='val')
            report = self.evaluator.report()
            self.reset_metrics()
            logger.info(nice_report(report))

    def fit(self, debug=False):
        r"""Train the model based on the train data.

        """
        self.pretrain(debug)
        self.train_recommender(debug)
        self.train_conversation(debug)