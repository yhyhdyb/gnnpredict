from torch import nn

# 这段代码实现了一个图卷积层的前向传递过程。
class GraphConvolution(nn.Module):
    def __init__(self, input_dim, output_dim, dropout, bias=False):
        super(GraphConvolution, self).__init__()
        #对输入矩阵进行随机失活
        self.dropout = nn.Dropout(dropout)
        #权重参数
        self.weight = nn.Parameter(torch.Tensor(input_dim, output_dim))
        nn.init.xavier_uniform_(self.weight)  # xavier初始化，就是论文里的glorot初始化
        if bias:
            self.bias = nn.Parameter(torch.Tensor(output_dim))
            nn.init.zeros_(self.bias)
        else:
            self.register_parameter('bias', None)

    def forward(self, inputs, adj):

        # inputs: (N, n_channels)——特征矩阵；adj: sparse_matrix (N, N)——邻接矩阵
        support = torch.mm(self.dropout(inputs), self.weight)
        output = torch.spmm(adj, support)
        if self.bias is not None:
            return output + self.bias
        else:
            return output


class GCN(nn.Module):
    def __init__(self, n_features, hidden_dim, dropout, n_classes):
        super(GCN, self).__init__()
        # 输入维度、输出维度和 Dropout 概率
        self.gc1 = GraphConvolution(n_features, hidden_dim, dropout)
        self.gc2 = GraphConvolution(hidden_dim, n_classes, dropout)
        # 激活函数
        self.relu = nn.ReLU()

    def forward(self, inputs, adj):
        x = inputs
        x = self.relu(self.gc1(x, adj))
        x = self.gc2(x, adj)
        return x


# %%

import torch
import torch.nn as nn
import torch.nn.functional as F


class GraphAttentionLayer(nn.Module):
    # 输入特征维度，输出特征维度 ，dropout概率，LeakyReLU 的负斜率，标志位
    def __init__(self, in_features, out_features, dropout, alpha, concat):
        super(GraphAttentionLayer, self).__init__()
        self.dropout = dropout
        self.in_features = in_features
        self.out_features = out_features
        self.alpha = alpha
        self.concat = concat

        # 两个可学习参数
        self.W = nn.Parameter(torch.zeros(in_features, out_features))
        self.a = nn.Parameter(torch.zeros(2 * out_features, 1))
        #  Xavier 初始化方法
        nn.init.xavier_uniform_(self.W.data, gain=1.414)
        nn.init.xavier_uniform_(self.a.data, gain=1.414)
        # 初始化了一个 LeakyReLU 激活函数
        self.leakyrelu = nn.LeakyReLU(self.alpha)

    def forward(self, h, adj):
        '''
        h: (N, in_features)
        adj: sparse matrix with shape (N, N)
        p
        '''
        adj=torch.squeeze(adj,-1)
        # print(h.dtype)
        # print(h.shape)

        # 对h线性变换，得到中间特征矩阵Wh
        Wh = torch.matmul(h, self.W)  # (N, out_features)

       #  分别对矩阵 Wh 的不同部分进行线性变换，得到注意力权重矩阵 Wh1 和 Wh2。
        Wh1 = torch.matmul(Wh, self.a[:self.out_features, :])  # (N, 1)
        Wh2 = torch.matmul(Wh, self.a[self.out_features:, :])  # (N, 1)
        # print(Wh1.shape)
        # print(Wh2.shape)

        # 通过将 Wh1 和 Wh2 进行转置，并相加得到注意力系数 e
        # Wh1 + Wh2.T 是N*N矩阵，第i行第j列是Wh1[i]+Wh2[j]
        # 那么Wh1 + Wh2.T的第i行第j列刚好就是文中的a^T*[Whi||Whj]
        # 代表着节点i对节点j的attention
        # print(torch.transpose(Wh2,2,1).shape)
        e = self.leakyrelu(Wh1 +torch.transpose(Wh2,2,1))  # (N, N)
        padding = (-2 ** 31) * torch.ones_like(e)  # (N, N)
        # print(adj.shape)
        # print(padding.shape)
        
        # 根据邻接矩阵 adj 及其与注意力系数 e 的比较，将无效的注意力系数置为负无穷大
        attention = torch.where(adj > 0, e, padding)  # (N, N)
        # 对有效的注意力系数进行 softmax 归一化
        attention = F.softmax(attention, dim=1)  # (N, N)
        # attention矩阵第i行第j列代表node_i对node_j的注意力
        # 对注意力权重也做dropout（如果经过mask之后，attention矩阵也许是高度稀疏的，这样做还有必要吗？）
        attention = F.dropout(attention, self.dropout, training=self.training)

        # 注意力系数加权求和  
        h_prime = torch.matmul(attention, Wh)  # (N, out_features)
        if self.concat:
            return F.elu(h_prime)
        else:
            return h_prime


class GAT(nn.Module):
    def __init__(self,date_emb, nfeat, nhid, dropout, alpha, nheads):
        super(GAT, self).__init__()
        date_index_number,date_dim = date_emb[0], date_emb[1]
        self.dropout = dropout

        # 输入特征维度 nfeat，隐藏层维度 nhid，Dropout 概率 dropout，LeakyReLU 的负斜率 alpha，以及注意力头数 nheads
        self.MH = nn.ModuleList([
            GraphAttentionLayer(nfeat, nhid, dropout, alpha, concat=True)
            for _ in range(nheads)
        ])
        
        self.out_att = GraphAttentionLayer(nhid * nheads, nhid, dropout, alpha, concat=False)
        self.date_embdding = nn.Embedding(date_index_number,date_dim)
        self.active_index = nn.Linear(nhid,1)
        self.consume_index = nn.Linear(nhid,1)

        
    def forward(self,x_date,x_feature,x_mask_data):


        x = x_feature
        # x = F.dropout(x_feature, self.dropout, training=self.training)  # (N, nfeat)
        x = torch.cat([head(x, x_mask_data) for head in self.MH], dim=-1)  # (N, nheads*nhid)
        x = F.dropout(x, self.dropout, training=self.training)  # (N, nfeat)


        # x = F.dropout(x, self.dropout, training=self.training)  # (N, nheads*nhid)
        x = self.out_att(x, x_mask_data)
        # print(x.shape,x.dtype)
        act_pre= self.active_index(x)
        con_pre = self.consume_index(x)
        return  act_pre,con_pre


class BILSTM(nn.Module):
    def __init__(self,date_emb, nfeat, nhid, dropout, alpha, nheads):
        super(BILSTM, self).__init__()
        date_index_number,date_dim = date_emb[0], date_emb[1]
        self.dropout = dropout
        self.lstm = nn.LSTM(nfeat,
                nhid,
                num_layers=2,
                bias=True,
                batch_first=False,
                dropout=0,
                bidirectional=True)

        self.active_index = nn.Linear(2*nhid, 1)
        self.consume_index = nn.Linear(2*nhid, 1)
    def forward(self,x_date,x_feature,x_mask_data):
        lstm_out, (hidden, cell) = self.lstm(x_feature)
        x = lstm_out
        # print(x.shape)

        x = F.dropout(x, self.dropout, training=self.training)  # (N, nheads*nhid)
        act_pre= self.active_index(x)
        con_pre = self.consume_index(x)
        # print(act_pre.shape,con_pre.shape)
        return  act_pre,con_pre