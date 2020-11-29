# @Time   : 2020/11/22
# @Author : Kun Zhou
# @Email  : francis_kun_zhou@163.com

# UPDATE:
# @Time   : 2020/11/24
# @Author : Kun Zhou
# @Email  : francis_kun_zhou@163.com

from .kgsf_system import KGSFSystem

system_register_table = {
    'KGSF': KGSFSystem
}


def get_system(config, train_dataloader, valid_dataloader, test_dataloader, ind2token, side_data=None):
    """
    return the system class
    """
    if config['rec_model'] in system_register_table:
        return system_register_table[config['rec_model']](config, train_dataloader,
                                                          valid_dataloader, test_dataloader, ind2token, side_data)
    else:
        raise NotImplementedError('The system with models [{}, {}] in dataset [{}] has not been implemented'.
                                  format(config['rec_model'], config['conv_model'], config['dataset']))
