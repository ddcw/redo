# 介绍
解析mysql redo文件的工具, 支持mysql 5.7/8.0

# 下载
```shell
wget https://github.com/ddcw/redo/archive/refs/heads/main.zip
unzip main.zip
cd redo-main/
python3 redo_reader.py --help
```

# 使用

## 解析整个redo文件
输出内容非常多,建议搭配more之类的命令使用
```shell
python3 redo_reader.py /data/mysql_5744/mysqllog/redolog/ib_logfile0
python3 redo_reader.py /data/mysql_5744/mysqllog/redolog/ib_logfile0 | strings | more
```

## 从某个lsn开始解析redo 
输出内容可能还是很多
```shell
python3 redo_reader.py /data/mysql_8037/mysqllog/redolog/#innodb_redo/#ib_redo40 --start-lsn 674431533
```

## 指定某个mlog, 
即只输出某种mlog
```shell
python3 redo_reader.py /data/mysql_8037/mysqllog/redolog/#innodb_redo/#ib_redo40 --start-lsn 674431533 --set name=MLOG_UNDO_INSERT
```

## 解析mlog获取被truncate表的indexid信息
```shell
python3 redo_reader.py /data/mysql_8037/mysqllog/redolog/#innodb_redo/#ib_redo40 --set name=MLOG_UNDO_INSERT | strings |grep ';space_id'
```
例子:
```shell
[root@ddcw21 redo-main]#python3 redo_reader.py /data/mysql_8037/mysqllog/redolog/#innodb_redo/#ib_redo40 --set name=MLOG_UNDO_INSERT --start-lsn 674431533 | strings |grep ';space_id' -C 4
- [S] file:#ib_redo40 gblockno:1317268 lsn:674441041 cblockno:6695 offset:337 name:MLOG_UNDO_INSERT(20)
    spaceno   : 4294967279
    pageno    : 278
    len       : 134
    data      : b'L\x00\x10\x13\x00\x00\x00\x0045\xc2\x00\x00\x01!\x06\x1d\x08\x00\x00\x00\x00\x00\x00\x01\xec\x02\x0e6id=374;root=4;space_id=162;table_id=1224;trx_id=13365;\x0f\x08\x00\x00\x00\x00\x00\x00\x00\xa7\x00)\x00\x08\x00\x00\x00\x00\x00\x00\x01\xec\x03\x08\x00\x00\x00\x00\x00\x00\x02\t\x04\x07PRIMARY\x0f\x08\x00\x00\x00\x00\x00\x00\x00\xa7'
- [S] file:#ib_redo40 gblockno:1317269 lsn:674441389 cblockno:6696 offset:173 name:MLOG_UNDO_INSERT(20)
    spaceno   : 4294967279
    pageno    : 278
    len       : 32
```
这里看到的id=374中的374就是indexid. 
其实这个信息是undo信息, 紧跟着就是MLOG_REC_UPDATE_IN_PLACE,这里面含rollptr,我们搭配[undo_reader](https://github.com/ddcw/ddcw/tree/master/python/undo_reader)也可以看到一样的信息:
```shell
[root@ddcw21 redo-main]#python3 redo_reader.py /data/mysql_8037/mysqllog/redolog/#innodb_redo/#ib_redo40 --start-lsn 674431533 --set name=MLOG_REC_UPDATE_IN_PLACE| strings | grep 'space_id' -B 17
- [S] file:#ib_redo40 gblockno:1317268 lsn:674441182 cblockno:6695 offset:478 name:MLOG_REC_UPDATE_IN_PLACE(70)
    spaceno   : 4294967294
    pageno    : 418
    index_version: 1
    index_flags: 1
    cols      : 19
    inst_cols : None
    n_uniq    : 1
    index_fields: [32776, 32774, 32775, 32776, 32768, 32769, 32769, 32769, 32769, 32769, 32769, 32772, 65535, 32767, 32767, 8, 32768, 32767, 32767]
    index_versioned_fields: None
    flags     : 2
    pos       : 1
    roll_ptr  : 281474994939033
    trx_id    : 16689
    rec_offset: 13641
    info_bits : 0
    n_fields  : 2
    fields    : [{'field_no': 14, 'field_len': 54, 'field_value': b'id=397;root=4;space_id=176;table_id=1238;trx_id=16689;'}, {'field_no': 15, 'field_len': 8, 'field_value': b'\x00\x00\x00\x00\x00\x00\x00\xb5'}]
[root@ddcw21 redo-main]#
[root@ddcw21 redo-main]#python3 undo_reader.py /data/mysql_8037/mysqldata/undo_001 --roll 281474994939033
PAGENO:278  OFFSET:9369 --> 9507  rseg_id:1  is_insert:False
DATA: b'L\x00\x10\x13\x00\x00\x00\x0045\xc2\x00\x00\x01!\x06\x1d\x08\x00\x00\x00\x00\x00\x00\x01\xec\x02\x0e6id=374;root=4;space_id=162;table_id=1224;trx_id=13365;\x0f\x08\x00\x00\x00\x00\x00\x00\x00\xa7\x00)\x00\x08\x00\x00\x00\x00\x00\x00\x01\xec\x03\x08\x00\x00\x00\x00\x00\x00\x02\t\x04\x07PRIMARY\x0f\x08\x00\x00\x00\x00\x00\x00\x00\xa7'
[root@ddcw21 redo-main]#
```
我们也找到了"id=374".
